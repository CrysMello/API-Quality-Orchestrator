export interface CliInvocation {
  args: string[];
  cwd: string;
}

export interface CliResult {
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  /**
   * stdout completo, além de já ter sido transmitido ao Output Channel.
   * Usado só quando um comando precisa extrair algo específico da saída
   * (ex.: report precisa achar o caminho do HTML gerado) — sempre de
   * forma defensiva, nunca crítica para o funcionamento do comando.
   */
  stdout: string;
}
