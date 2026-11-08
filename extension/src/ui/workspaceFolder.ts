import * as vscode from "vscode";

// artifacts/ é sempre relativo ao cwd do processo Python (confirmado lendo
// local_artifact_repository.py / json_execution_result_repository.py) —
// por isso generate/run/report SEMPRE precisam de um cwd explícito, nunca
// o default do Node (que seria a pasta da própria extensão).
let cachedFolderNoWorkspace: string | undefined;

/**
 * Resolve onde generate/run/report devem rodar. Zero workspace aberto ->
 * pede pra escolher uma pasta (cacheado em memória, só pergunta uma vez
 * por sessão do VSCode); um workspace -> usa direto; múltiplos ->
 * QuickPick. Retorna undefined se o usuário cancelar a escolha.
 */
export async function resolveCwd(): Promise<string | undefined> {
  const folders = vscode.workspace.workspaceFolders;

  if (folders && folders.length === 1) {
    return folders[0].uri.fsPath;
  }

  if (folders && folders.length > 1) {
    const picked = await vscode.window.showQuickPick(
      folders.map((folder) => ({ label: folder.name, description: folder.uri.fsPath, folder })),
      { placeHolder: "Em qual pasta do workspace rodar? (é onde artifacts/ será criado)" },
    );
    return picked?.folder.uri.fsPath;
  }

  if (cachedFolderNoWorkspace) {
    return cachedFolderNoWorkspace;
  }

  const uris = await vscode.window.showOpenDialog({
    canSelectFiles: false,
    canSelectFolders: true,
    canSelectMany: false,
    openLabel: "Usar esta pasta (onde artifacts/ será criado)",
    title: "Nenhuma pasta de workspace aberta — escolha onde rodar o comando",
  });
  if (!uris || uris.length === 0) {
    return undefined;
  }
  cachedFolderNoWorkspace = uris[0].fsPath;
  return cachedFolderNoWorkspace;
}
