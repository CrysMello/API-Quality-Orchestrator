import * as os from "os";
import * as vscode from "vscode";
import { runCli } from "../cli/cliRunner";
import { describeExitCode } from "../cli/exitCodes";
import { getCliEnvironment } from "../pythonEnv/venvManager";
import { showExitCodeNotification } from "../ui/prompts";
import { withErrorBoundary } from "./errorBoundary";

// Sem prompts, args fixos — reaproveita o mesmo pipeline já validado
// pelo doctor (bootstrap -> spawn -> exit code).
export function registerVersionCommand(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): void {
  const command = vscode.commands.registerCommand(
    "apiQuality.version",
    withErrorBoundary(outputChannel, async () => {
      const env = await getCliEnvironment(context, outputChannel);
      const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? os.homedir();

      const result = await runCli(env, { args: ["version"], cwd }, outputChannel);

      const info = describeExitCode(result.exitCode);
      await showExitCodeNotification(outputChannel, info, "Version");
    }),
  );
  context.subscriptions.push(command);
}
