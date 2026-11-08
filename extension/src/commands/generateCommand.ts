import * as path from "path";
import * as vscode from "vscode";
import { runCli } from "../cli/cliRunner";
import { describeExitCode } from "../cli/exitCodes";
import { getCliEnvironment } from "../pythonEnv/venvManager";
import { showExitCodeNotification } from "../ui/prompts";
import { resolveCwd } from "../ui/workspaceFolder";
import { withErrorBoundary } from "./errorBoundary";

type Target = "postman" | "playwright" | "all";

async function pickJsonFile(openLabel: string, title: string): Promise<string | undefined> {
  const uris = await vscode.window.showOpenDialog({
    canSelectFiles: true,
    canSelectFolders: false,
    canSelectMany: false,
    filters: { JSON: ["json"] },
    openLabel,
    title,
  });
  return uris?.[0]?.fsPath;
}

export function registerGenerateCommand(context: vscode.ExtensionContext, outputChannel: vscode.OutputChannel): void {
  const command = vscode.commands.registerCommand(
    "apiQuality.generate",
    withErrorBoundary(outputChannel, async () => {
      const env = await getCliEnvironment(context, outputChannel);

      // "sourceKind" (não "kind") de propósito: QuickPickItem já tem um
      // campo `kind` embutido (QuickPickItemKind, usado para separadores)
      // — reaproveitar esse nome quebra a checagem de tipos do TS.
      const source = await vscode.window.showQuickPick(
        [
          { label: "Collection Postman (.json)", sourceKind: "collection" as const },
          { label: "Especificação OpenAPI/Swagger (.json)", sourceKind: "openapi" as const },
        ],
        { placeHolder: "A partir de qual arquivo gerar os testes?" },
      );
      if (!source) return;

      const filePath = await pickJsonFile(
        "Usar este arquivo",
        source.sourceKind === "collection" ? "Selecione a Collection Postman (.json)" : "Selecione a especificação OpenAPI/Swagger (.json)",
      );
      if (!filePath) return;

      const targetChoice = await vscode.window.showQuickPick(
        [
          { label: "Postman", target: "postman" as Target, description: "padrão" },
          { label: "Playwright", target: "playwright" as Target },
          { label: "Ambos", target: "all" as Target },
        ],
        { placeHolder: "Gerar testes para qual destino?" },
      );
      if (!targetChoice) return;

      const wantsEnvironment = await vscode.window.showQuickPick(
        [
          { label: "Não informar Environment", withEnv: false },
          { label: "Informar um arquivo de Environment (.json)...", withEnv: true },
        ],
        { placeHolder: "Usar um Environment do Postman? (opcional, só afeta a geração Playwright)" },
      );
      if (!wantsEnvironment) return;

      let environmentPath: string | undefined;
      if (wantsEnvironment.withEnv) {
        environmentPath = await pickJsonFile("Usar este Environment", "Selecione o arquivo de Environment (.json)");
        if (!environmentPath) return;
      }

      const cwd = await resolveCwd();
      if (!cwd) return;

      const sourceLabel = source.sourceKind === "collection" ? "Collection Postman" : "Especificação OpenAPI/Swagger";
      const confirmation = await vscode.window.showWarningMessage(
        `Gerar testes (${targetChoice.label}) a partir de:\n${sourceLabel}: ${filePath}` +
          (environmentPath ? `\nEnvironment: ${environmentPath}` : "") +
          `\nPasta de destino (artifacts/): ${cwd}`,
        { modal: true },
        "Gerar",
      );
      if (confirmation !== "Gerar") return;

      // --yes sempre: sem TTY interativo num child_process, o prompt de
      // confirmação do próprio CLI (via stdin) travaria. A confirmação
      // acima, feita pela extensão, substitui esse prompt.
      const args = [
        "generate",
        "--yes",
        "--target",
        targetChoice.target,
        source.sourceKind === "collection" ? "--file" : "--openapi-file",
        filePath,
        ...(environmentPath ? ["-e", environmentPath] : []),
      ];

      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Gerando testes...", cancellable: false },
        () => runCli(env, { args, cwd }, outputChannel),
      );

      const info = describeExitCode(result.exitCode);
      await showExitCodeNotification(outputChannel, info, "Generate");

      if (result.exitCode === 0) {
        const artifactsDir = path.join(cwd, "artifacts");
        const openChoice = await vscode.window.showInformationMessage(
          "Testes gerados.",
          "Revelar no Explorador de Arquivos",
        );
        if (openChoice) {
          await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(artifactsDir));
        }
      }
    }),
  );
  context.subscriptions.push(command);
}
