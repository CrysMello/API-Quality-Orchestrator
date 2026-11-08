import * as vscode from "vscode";

let channel: vscode.OutputChannel | null = null;

/** Canal único, criado sob demanda, compartilhado por todos os comandos. */
export function getOutputChannel(): vscode.OutputChannel {
  if (!channel) {
    channel = vscode.window.createOutputChannel("API Quality Orchestrator");
  }
  return channel;
}
