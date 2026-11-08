import { spawn } from "child_process";
import * as vscode from "vscode";
import { CliEnvironment } from "../pythonEnv/types";
import { CliInvocation, CliResult } from "./types";

/**
 * Executa a CLI via `venvPythonExe -m api_quality_agent.cli.main <args>`
 * (nunca o shim .exe gerado em Scripts/ — evita depender de mais um
 * executável recém-criado que antivírus poderia atrasar/bloquear).
 *
 * Sempre `spawn` com array de args e shell:false — nunca `exec`/string de
 * comando, o que eliminaria qualquer risco de parsing incorreto de
 * caminhos com espaço (ex.: "OneDrive\Documentos\...").
 */
export function runCli(
  env: CliEnvironment,
  invocation: CliInvocation,
  outputChannel: vscode.OutputChannel,
  token?: vscode.CancellationToken,
): Promise<CliResult> {
  outputChannel.show(true);
  outputChannel.appendLine(`\n> api-quality-orchestrator ${invocation.args.join(" ")}`);
  outputChannel.appendLine(`  (cwd: ${invocation.cwd})`);

  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(env.pythonExe, ["-m", "api_quality_agent.cli.main", ...invocation.args], {
        cwd: invocation.cwd,
        shell: false,
        windowsHide: true,
      });
    } catch (error) {
      reject(error);
      return;
    }

    const cancelListener = token?.onCancellationRequested(() => {
      outputChannel.appendLine("[cancelado pelo usuário]");
      child.kill();
    });

    let stdout = "";
    child.stdout?.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8");
      stdout += text;
      outputChannel.append(text);
    });
    child.stderr?.on("data", (chunk: Buffer) => outputChannel.append(chunk.toString("utf8")));

    child.on("error", (error) => {
      cancelListener?.dispose();
      reject(error);
    });

    child.on("close", (code, signal) => {
      cancelListener?.dispose();
      resolve({ exitCode: code, signal, stdout });
    });
  });
}
