import * as vscode from "vscode";
import { runCli } from "../cli/cliRunner";
import { describeExitCode } from "../cli/exitCodes";
import { getCliEnvironment } from "../pythonEnv/venvManager";
import { showExitCodeNotification } from "../ui/prompts";
import { resolveCwd } from "../ui/workspaceFolder";
import { withErrorBoundary } from "./errorBoundary";
import { runReportAndOpen } from "./reportCommand";

type Target = "postman" | "playwright";

export function registerRunCommand(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): void {
  const command = vscode.commands.registerCommand(
    "apiQuality.run",
    withErrorBoundary(outputChannel, async () => {
      const env = await getCliEnvironment(context, outputChannel);

      // Só postman/playwright — run --target NÃO aceita "all" (diferente
      // de generate; confirmado lendo run_command.py).
      const targetChoice = await vscode.window.showQuickPick(
        [
          { label: "Postman (via Newman)", target: "postman" as Target, description: "padrão" },
          { label: "Playwright (via pytest)", target: "playwright" as Target },
        ],
        { placeHolder: "Executar qual suíte de testes?" },
      );
      if (!targetChoice) return;

      let filePath: string | undefined;
      if (targetChoice.target === "postman") {
        const uris = await vscode.window.showOpenDialog({
          canSelectFiles: true,
          canSelectFolders: false,
          canSelectMany: false,
          filters: { JSON: ["json"] },
          openLabel: "Usar esta Collection",
          title: "Selecione a Collection Postman exportada (.json)",
        });
        filePath = uris?.[0]?.fsPath;
      } else {
        const uris = await vscode.window.showOpenDialog({
          canSelectFiles: false,
          canSelectFolders: true,
          canSelectMany: false,
          openLabel: "Usar esta suíte",
          title: "Selecione a pasta da suíte Playwright já gerada",
        });
        filePath = uris?.[0]?.fsPath;
      }
      if (!filePath) return;

      const wantsEnvironment = await vscode.window.showQuickPick(
        [
          { label: "Não informar Environment", withEnv: false },
          { label: "Informar um arquivo de Environment (.json)...", withEnv: true },
        ],
        { placeHolder: "Usar um Environment do Postman? (opcional)" },
      );
      if (!wantsEnvironment) return;

      let environmentPath: string | undefined;
      if (wantsEnvironment.withEnv) {
        const uris = await vscode.window.showOpenDialog({
          canSelectFiles: true,
          canSelectFolders: false,
          canSelectMany: false,
          filters: { JSON: ["json"] },
          openLabel: "Usar este Environment",
        });
        environmentPath = uris?.[0]?.fsPath;
        if (!environmentPath) return;
      }

      const cwd = await resolveCwd();
      if (!cwd) return;

      // run não tem -y/--yes (não existe essa flag no parser real) —
      // não prompta via stdin, não é destrutivo. Sem modal de
      // confirmação aqui de propósito (diferente de generate).
      const args = [
        "run",
        "--target",
        targetChoice.target,
        "-f",
        filePath,
        ...(environmentPath ? ["-e", environmentPath] : []),
      ];

      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Executando testes...", cancellable: true },
        (_progress, token) => runCli(env, { args, cwd }, outputChannel, token),
      );

      const info = describeExitCode(result.exitCode);

      // Exit 0 (sucesso) e 1 (falhas funcionais) ambos persistem
      // result.json — só falha de infraestrutura (6) não persiste nada.
      if (result.exitCode === 0 || result.exitCode === 1) {
        const message = `Run: ${info.message}`;
        const openChoice =
          result.exitCode === 0
            ? await vscode.window.showInformationMessage(message, "Abrir Relatório", "Ver logs")
            : await vscode.window.showWarningMessage(message, "Abrir Relatório", "Ver logs");
        if (openChoice === "Ver logs") {
          outputChannel.show(true);
        } else if (openChoice === "Abrir Relatório") {
          await runReportAndOpen(env, cwd, outputChannel);
        }
        return;
      }

      if (result.exitCode === 6) {
        const hint =
          targetChoice.target === "postman"
            ? "Verifique se o Newman está instalado (npm install -g newman) e acessível no PATH."
            : "Verifique se o pytest/Playwright estão instalados no ambiente correto e acessíveis no PATH.";
        outputChannel.appendLine(`[dica] ${hint}`);
        await showExitCodeNotification(outputChannel, info, `Run (${hint})`);
        return;
      }

      await showExitCodeNotification(outputChannel, info, "Run");
    }),
  );
  context.subscriptions.push(command);
}
