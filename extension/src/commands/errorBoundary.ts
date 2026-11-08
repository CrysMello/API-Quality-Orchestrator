import * as vscode from "vscode";

/**
 * Envolve o handler de um comando pra nunca deixar uma exceção não
 * tratada "crua" chegar ao usuário — sempre vira uma mensagem de erro
 * com botão pra abrir os logs.
 */
export function withErrorBoundary(
  outputChannel: vscode.OutputChannel,
  handler: () => Promise<void>,
): () => Promise<void> {
  return async () => {
    try {
      await handler();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      outputChannel.appendLine(`[erro] ${message}`);
      const choice = await vscode.window.showErrorMessage(`API Quality Orchestrator: ${message}`, "Ver logs");
      if (choice === "Ver logs") {
        outputChannel.show(true);
      }
    }
  };
}
