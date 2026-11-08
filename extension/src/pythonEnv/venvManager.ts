import { spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { PythonNotFoundError, resolveSystemInterpreter } from "./interpreterResolver";
import { readBundledManifest } from "./manifest";
import { BootstrapState, CliEnvironment, ResolvedInterpreter } from "./types";

const VENV_DIR_NAME = "python-env";
const STATE_FILE_NAME = "bootstrap-state.json";
const HEALTH_CHECK_TIMEOUT_MS = 5000;
const VENV_CREATE_TIMEOUT_MS = 60000;
const PIP_INSTALL_TIMEOUT_MS = 120000;
const VERSION_CHECK_TIMEOUT_MS = 15000;

// Cache em módulo: garante que duas invocações de comando quase
// simultâneas (ex.: usuário clica em dois comandos rapidamente) nunca
// disparem duas criações de venv em paralelo — todo mundo espera a
// mesma Promise.
let cachedReadyPromise: Promise<CliEnvironment> | null = null;

function venvPythonExePath(venvDir: string): string {
  return path.join(venvDir, "Scripts", "python.exe");
}

function readState(stateFile: string): BootstrapState | null {
  try {
    const raw = fs.readFileSync(stateFile, "utf8");
    const parsed = JSON.parse(raw);
    if (typeof parsed.packageVersion === "string" && Array.isArray(parsed.wheelFileNames)) {
      return parsed as BootstrapState;
    }
    return null;
  } catch {
    return null;
  }
}

function writeState(stateFile: string, state: BootstrapState): void {
  fs.mkdirSync(path.dirname(stateFile), { recursive: true });
  fs.writeFileSync(stateFile, JSON.stringify(state, null, 2), "utf8");
}

function sameWheelSet(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort();
  const sortedB = [...b].sort();
  return sortedA.every((name, i) => name === sortedB[i]);
}

interface RunResult {
  stdout: string;
  stderr: string;
}

function runChecked(command: string, args: string[], timeoutMs: number): Promise<RunResult> {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(command, args, { shell: false, windowsHide: true });
    } catch (error) {
      reject(error);
      return;
    }

    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, timeoutMs);

    child.stdout?.on("data", (chunk: Buffer) => (stdout += chunk.toString("utf8")));
    child.stderr?.on("data", (chunk: Buffer) => (stderr += chunk.toString("utf8")));

    child.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      if (timedOut) {
        reject(new Error(`Comando '${command} ${args.join(" ")}' excedeu o tempo limite (${timeoutMs}ms).`));
        return;
      }
      if (code !== 0) {
        reject(new Error(`Comando '${command} ${args.join(" ")}' falhou (exit ${code}):\n${stderr || stdout}`));
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

async function healthCheck(venvPythonExe: string): Promise<boolean> {
  if (!fs.existsSync(venvPythonExe)) return false;
  try {
    await runChecked(venvPythonExe, ["-c", "import api_quality_agent"], HEALTH_CHECK_TIMEOUT_MS);
    return true;
  } catch {
    return false;
  }
}

async function handlePythonNotFound(error: PythonNotFoundError): Promise<never> {
  const details = error.insufficientVersions.map((v) => `${v.executable} (${v.version.join(".")})`).join(", ");
  const suffix = details ? ` Encontrado(s), mas insuficiente(s): ${details}.` : "";
  const choice = await vscode.window.showErrorMessage(
    `Não foi encontrado nenhum Python 3.12+ no sistema (tentado: python, python3, py -3).${suffix} ` +
      'Instale o Python 3.12 ou superior e marque "Add python.exe to PATH" durante a instalação.',
    "Abrir python.org",
  );
  if (choice === "Abrir python.org") {
    void vscode.env.openExternal(vscode.Uri.parse("https://www.python.org/downloads/"));
  }
  throw error;
}

async function installFromBundledWheels(
  venvPythonExe: string,
  resourcesDir: string,
  outputChannel: vscode.OutputChannel,
): Promise<void> {
  try {
    const { stdout, stderr } = await runChecked(
      venvPythonExe,
      [
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        resourcesDir,
        "--disable-pip-version-check",
        "--no-cache-dir",
        "api-quality-orchestrator",
      ],
      PIP_INSTALL_TIMEOUT_MS,
    );
    if (stdout) outputChannel.appendLine(stdout);
    if (stderr) outputChannel.appendLine(stderr);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes("No matching distribution")) {
      throw new Error(
        "Falha ao instalar a partir dos wheels embutidos: nenhuma distribuição compatível encontrada. " +
          "Isso normalmente indica que a extensão foi empacotada para uma versão/plataforma de Python " +
          "diferente da deste computador (ver extension/scripts/prepare-release.ps1). " +
          `Detalhe original: ${message}`,
      );
    }
    throw error;
  }
}

async function performBootstrap(
  context: vscode.ExtensionContext,
  outputChannel: vscode.OutputChannel,
): Promise<CliEnvironment> {
  const storageDir = context.globalStorageUri.fsPath;
  fs.mkdirSync(storageDir, { recursive: true });

  const venvDir = path.join(storageDir, VENV_DIR_NAME);
  const stateFile = path.join(storageDir, STATE_FILE_NAME);
  const venvPythonExe = venvPythonExePath(venvDir);

  const manifest = readBundledManifest(context.extensionUri.fsPath);
  const state = readState(stateFile);
  const versionMatches =
    state !== null &&
    state.packageVersion === manifest.packageVersion &&
    sameWheelSet(state.wheelFileNames, manifest.wheelFileNames);

  if (versionMatches && (await healthCheck(venvPythonExe))) {
    outputChannel.appendLine(`[bootstrap] Ambiente já preparado (versão ${manifest.packageVersion}); reaproveitando.`);
    return { pythonExe: venvPythonExe };
  }

  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "API Quality Orchestrator: preparando ambiente Python...",
      cancellable: false,
    },
    async (progress) => {
      outputChannel.show(true);
      outputChannel.appendLine("[bootstrap] Preparando ambiente Python isolado (primeira vez, ou nova versão)...");

      progress.report({ message: "Procurando um interpretador Python (>=3.12)..." });
      let interpreter: ResolvedInterpreter;
      try {
        interpreter = await resolveSystemInterpreter();
      } catch (error) {
        if (error instanceof PythonNotFoundError) {
          await handlePythonNotFound(error);
        }
        throw error;
      }
      outputChannel.appendLine(`[bootstrap] Python encontrado: ${interpreter.executable} (${interpreter.version.join(".")})`);

      progress.report({ message: "Criando ambiente isolado..." });
      if (fs.existsSync(venvDir)) {
        fs.rmSync(venvDir, { recursive: true, force: true });
      }
      try {
        await runChecked(interpreter.executable, ["-m", "venv", venvDir], VENV_CREATE_TIMEOUT_MS);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        // Reproduzido de verdade durante o desenvolvimento: com o Windows
        // Long Path support desabilitado (padrão de fábrica), criar o
        // venv num caminho muito longo/aninhado falha com esse erro
        // específico do próprio ensurepip. globalStorage costuma ser
        // curto o suficiente para nunca bater nisso, mas perfis de
        // usuário com nomes muito longos podem esbarrar aqui.
        if (message.includes("Long Path")) {
          throw new Error(
            `Falha ao criar o ambiente Python isolado em '${venvDir}': o Windows Long Path support parece estar ` +
              "desabilitado e esse caminho é longo demais. Ative em " +
              "https://pip.pypa.io/warnings/enable-long-paths e tente de novo. " +
              `Detalhe original: ${message}`,
          );
        }
        throw error;
      }

      progress.report({ message: "Instalando api-quality-orchestrator (offline, a partir dos wheels embutidos)..." });
      const resourcesDir = path.join(context.extensionUri.fsPath, "resources", "python-dist");
      await installFromBundledWheels(venvPythonExe, resourcesDir, outputChannel);

      progress.report({ message: "Validando instalação..." });
      await runChecked(venvPythonExe, ["-m", "api_quality_agent.cli.main", "--version"], VERSION_CHECK_TIMEOUT_MS);

      const newState: BootstrapState = {
        packageVersion: manifest.packageVersion,
        wheelFileNames: manifest.wheelFileNames,
        pythonInterpreterUsed: interpreter,
        venvPythonExe,
        installedAt: new Date().toISOString(),
      };
      writeState(stateFile, newState);
      outputChannel.appendLine("[bootstrap] Ambiente pronto.");

      return { pythonExe: venvPythonExe };
    },
  );
}

/**
 * Garante que o venv isolado existe e tem o pacote instalado, criando-o
 * se necessário. Idempotente e seguro para chamar a cada comando — o
 * caminho feliz (ambiente já pronto) é rápido (health check curto).
 */
export function getCliEnvironment(
  context: vscode.ExtensionContext,
  outputChannel: vscode.OutputChannel,
): Promise<CliEnvironment> {
  if (!cachedReadyPromise) {
    cachedReadyPromise = performBootstrap(context, outputChannel).catch((error) => {
      // Não deixa uma falha ficar em cache bloqueando tentativas futuras
      // nesta mesma sessão do VSCode (ex.: usuário instalou o Python e
      // quer tentar de novo sem precisar recarregar a janela).
      cachedReadyPromise = null;
      throw error;
    });
  }
  return cachedReadyPromise;
}
