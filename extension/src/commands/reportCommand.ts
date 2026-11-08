import * as vscode from "vscode";
import { runCli } from "../cli/cliRunner";
import { describeExitCode } from "../cli/exitCodes";
import { CliEnvironment } from "../pythonEnv/types";
import { getCliEnvironment } from "../pythonEnv/venvManager";
import { showExitCodeNotification } from "../ui/prompts";
import { resolveCwd } from "../ui/workspaceFolder";
import { withErrorBoundary } from "./errorBoundary";

// Formato estável de report_command.py (_print_success): "Output:\n  <path>\n".
// Parsing defensivo e opcional — se não achar, só não abre automaticamente
// (nunca quebra o comando).
const OUTPUT_PATH_PATTERN = /Output:\r?\n\s+(.+)\r?\n/;

function extractReportPath(stdout: string): string | undefined {
  const match = OUTPUT_PATH_PATTERN.exec(stdout);
  return match?.[1]?.trim();
}

/**
 * Roda `report` (opcionalmente com -i) e abre o HTML gerado no navegador
 * padrão. Reaproveitado tanto pelo comando apiQuality.report quanto pelo
 * botão "Abrir Relatório" do apiQuality.run (que sempre quer o mais
 * recente, sem perguntar de novo).
 */
export async function runReportAndOpen(
  env: CliEnvironment,
  cwd: string,
  outputChannel: vscode.OutputChannel,
  inputPath?: string,
): Promise<void> {
  // --overwrite sempre: report.html é artefato derivado/regenerável,
  // nunca fonte de verdade — sempre sobrescrever evita um
  // ReportAlreadyExistsError confuso ao gerar duas vezes seguidas.
  const args = ["report", ...(inputPath ? ["-i", inputPath] : []), "--overwrite"];

  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Gerando relatório...", cancellable: false },
    () => runCli(env, { args, cwd }, outputChannel),
  );

  const info = describeExitCode(result.exitCode);
  await showExitCodeNotification(outputChannel, info, "Report");

  if (result.exitCode === 0) {
    const reportPath = extractReportPath(result.stdout);
    if (reportPath) {
      await vscode.env.openExternal(vscode.Uri.file(reportPath));
    } else {
      outputChannel.appendLine("[aviso] Não foi possível localizar o caminho do relatório na saída — veja o Output Channel.");
    }
  }
}

export function registerReportCommand(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): void {
  const command = vscode.commands.registerCommand(
    "apiQuality.report",
    withErrorBoundary(outputChannel, async () => {
      const env = await getCliEnvironment(context, outputChannel);

      const choice = await vscode.window.showQuickPick(
        [
          { label: "Mais recente (automático)", specific: false },
          { label: "Selecionar result.json específico...", specific: true },
        ],
        { placeHolder: "Qual resultado de execução usar para o relatório?" },
      );
      if (!choice) return;

      let inputPath: string | undefined;
      if (choice.specific) {
        const uris = await vscode.window.showOpenDialog({
          canSelectFiles: true,
          canSelectFolders: false,
          canSelectMany: false,
          filters: { "result.json": ["json"] },
          openLabel: "Usar este result.json",
        });
        if (!uris || uris.length === 0) return;
        inputPath = uris[0].fsPath;
      }

      const cwd = await resolveCwd();
      if (!cwd) return;

      await runReportAndOpen(env, cwd, outputChannel, inputPath);
    }),
  );
  context.subscriptions.push(command);
}
