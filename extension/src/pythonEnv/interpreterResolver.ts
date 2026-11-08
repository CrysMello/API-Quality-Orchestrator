import { spawn } from "child_process";
import { ResolvedInterpreter } from "./types";

// Script mínimo executado com `-c` em cada candidato: imprime um JSON com
// o executável real e a versão. Nunca confiamos só em `--version`/exit
// code — isso descarta silenciosamente os stubs "python.exe" da
// Microsoft Store (existem no PATH em máquinas sem Python instalado de
// verdade, mas não executam código: abrem a Store ou falham sem stdout
// parseável).
const PROBE_SCRIPT =
  "import sys, json; print(json.dumps({'executable': sys.executable, 'version': list(sys.version_info[:3])}))";

const MIN_VERSION: readonly [number, number] = [3, 12];
const PROBE_TIMEOUT_MS = 5000;

const CANDIDATES: ReadonlyArray<{ command: string; args: string[] }> = [
  { command: "python", args: [] },
  { command: "python3", args: [] },
  { command: "py", args: ["-3"] },
];

export class PythonNotFoundError extends Error {
  constructor(public readonly insufficientVersions: ResolvedInterpreter[]) {
    super("Nenhum interpretador Python >=3.12 foi encontrado no sistema.");
    this.name = "PythonNotFoundError";
  }
}

function isVersionSufficient(version: readonly [number, number, number]): boolean {
  const [major, minor] = version;
  return major > MIN_VERSION[0] || (major === MIN_VERSION[0] && minor >= MIN_VERSION[1]);
}

function probe(command: string, extraArgs: string[]): Promise<ResolvedInterpreter | null> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: ResolvedInterpreter | null) => {
      if (!settled) {
        settled = true;
        resolve(value);
      }
    };

    let child;
    try {
      child = spawn(command, [...extraArgs, "-c", PROBE_SCRIPT], {
        shell: false,
        windowsHide: true,
      });
    } catch {
      finish(null);
      return;
    }

    const timer = setTimeout(() => {
      child.kill();
      finish(null);
    }, PROBE_TIMEOUT_MS);

    let stdout = "";
    child.stdout?.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.on("error", () => {
      clearTimeout(timer);
      finish(null);
    });
    child.on("close", () => {
      clearTimeout(timer);
      try {
        const parsed = JSON.parse(stdout.trim());
        if (
          parsed &&
          typeof parsed.executable === "string" &&
          Array.isArray(parsed.version) &&
          parsed.version.length === 3 &&
          parsed.version.every((n: unknown) => typeof n === "number")
        ) {
          finish({
            executable: parsed.executable,
            version: parsed.version as [number, number, number],
          });
          return;
        }
      } catch {
        // stdout não era o JSON esperado — candidato descartado.
      }
      finish(null);
    });
  });
}

/**
 * Tenta `python`, depois `python3`, depois `py -3`, nessa ordem. Retorna o
 * primeiro candidato com versão >=3.12. Se algum candidato responder mas
 * com versão insuficiente, continua tentando os próximos — só desiste
 * (lançando PythonNotFoundError) depois de esgotar todos.
 */
export async function resolveSystemInterpreter(): Promise<ResolvedInterpreter> {
  const insufficientVersions: ResolvedInterpreter[] = [];

  for (const candidate of CANDIDATES) {
    const result = await probe(candidate.command, candidate.args);
    if (result === null) {
      continue;
    }
    if (isVersionSufficient(result.version)) {
      return result;
    }
    insufficientVersions.push(result);
  }

  throw new PythonNotFoundError(insufficientVersions);
}
