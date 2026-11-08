import * as os from "os";
import * as vscode from "vscode";
import { runCli } from "../cli/cliRunner";
import { describeExitCode } from "../cli/exitCodes";
import { getCliEnvironment } from "../pythonEnv/venvManager";
import { showExitCodeNotification } from "../ui/prompts";
import { withErrorBoundary } from "./errorBoundary";

// Primeiro comando implementado de propósito: não cria nem altera nada
// (só lê versão do Python e acesso ao cwd), então exercita bootstrap +
// spawn + mapeamento de exit code de ponta a ponta com risco mínimo.
export function registerDoctorCommand(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): void {
  const command = vscode.commands.registerCommand(
    "apiQuality.doctor",
    withErrorBoundary(outputChannel, async () => {
      const env = await getCliEnvironment(context, outputChannel);
      const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? os.homedir();

      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Rodando doctor...", cancellable: false },
        () => runCli(env, { args: ["doctor"], cwd }, outputChannel),
      );

      const info = describeExitCode(result.exitCode);
      await showExitCodeNotification(outputChannel, info, "Doctor");
    }),
  );
  context.subscriptions.push(command);
}
