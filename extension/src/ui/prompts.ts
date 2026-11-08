import * as vscode from "vscode";
import { ExitCodeInfo } from "../cli/exitCodes";

/**
 * Mostra o resultado de um exit code como notificação (info/warning/error
 * conforme a severidade), com um botão "Ver logs" que abre o Output
 * Channel. Escrito como if/else explícito (em vez de guardar
 * showErrorMessage/showWarningMessage/showInformationMessage numa
 * variável) de propósito — essas funções têm overloads que o TypeScript
 * resolve mal quando atribuídas a uma variável genérica.
 */
export async function showExitCodeNotification(
  outputChannel: vscode.OutputChannel,
  info: ExitCodeInfo,
  prefix: string,
): Promise<void> {
  const message = `${prefix}: ${info.message}`;
  let choice: string | undefined;

  if (info.severity === "error") {
    choice = await vscode.window.showErrorMessage(message, "Ver logs");
  } else if (info.severity === "warning") {
    choice = await vscode.window.showWarningMessage(message, "Ver logs");
  } else {
    choice = await vscode.window.showInformationMessage(message, "Ver logs");
  }

  if (choice === "Ver logs") {
    outputChannel.show(true);
  }
}
