export interface BootstrapManifest {
  packageVersion: string;
  pythonTag: string;
  wheelFileNames: string[];
  generatedAt: string;
}

export interface ResolvedInterpreter {
  executable: string;
  version: [number, number, number];
}

export interface BootstrapState {
  packageVersion: string;
  wheelFileNames: string[];
  pythonInterpreterUsed: ResolvedInterpreter;
  venvPythonExe: string;
  installedAt: string;
}

/** Ambiente Python já pronto (venv isolado, pacote instalado). */
export interface CliEnvironment {
  pythonExe: string;
}
