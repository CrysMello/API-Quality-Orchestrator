import * as os from "os";
import * as vscode from "vscode";
import { runCli } from "../cli/cliRunner";
import { describeExitCode } from "../cli/exitCodes";
import { getCliEnvironment } from "../pythonEnv/venvManager";
import { showExitCodeNotification } from "../ui/prompts";
import { withErrorBoundary } from "./errorBoundary";

// Sem prompts, args fixos, sem parsing de stdout — decisão deliberada
// (parsear texto livre em português seria frágil; o Output Channel já
// mostra tudo).
export function registerConfigShowCommand(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): void {
  const command = vscode.commands.registerCommand(
    "apiQuality.configShow",
    withErrorBoundary(outputChannel, async () => {
      const env = await getCliEnvironment(context, outputChannel);
      const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? os.homedir();

      const result = await runCli(env, { args: ["config", "show"], cwd }, outputChannel);

      const info = describeExitCode(result.exitCode);
      await showExitCodeNotification(outputChannel, info, "Config");
    }),
  );
  context.subscriptions.push(command);
}
