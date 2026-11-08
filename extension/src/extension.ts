import * as vscode from "vscode";
import { registerCommands } from "./commands/registerCommands";

export function activate(context: vscode.ExtensionContext): void {
  registerCommands(context);
}

export function deactivate(): void {
  // Nada a limpar explicitamente — o Output Channel e os comandos são
  // descartados via context.subscriptions automaticamente pelo VSCode.
}
