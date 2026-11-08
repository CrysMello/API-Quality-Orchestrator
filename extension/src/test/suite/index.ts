import * as vscode from "vscode";

// Roda DENTRO do Extension Host real (via @vscode/test-electron), não é
// um mock. Smoke test do gate mais importante do plano: o comando
// `doctor` existe, e executá-lo dispara o bootstrap real (venv +
// instalação dos wheels embutidos) sem lançar exceção.
export async function run(): Promise<void> {
  const commands = await vscode.commands.getCommands(true);
  if (!commands.includes("apiQuality.doctor")) {
    throw new Error("Comando 'apiQuality.doctor' não foi registrado pela extensão.");
  }

  console.log("[test] Executando apiQuality.doctor (primeira vez — deve criar o venv do zero)...");
  const start1 = Date.now();
  await vscode.commands.executeCommand("apiQuality.doctor");
  console.log(`[test] Primeira execução concluída em ${Date.now() - start1}ms.`);

  console.log("[test] Executando apiQuality.doctor de novo (deve reaproveitar o venv — fast path)...");
  const start2 = Date.now();
  await vscode.commands.executeCommand("apiQuality.doctor");
  const duration2 = Date.now() - start2;
  console.log(`[test] Segunda execução concluída em ${duration2}ms.`);

  if (duration2 > 10000) {
    throw new Error(
      `Segunda execução do doctor levou ${duration2}ms — esperado um "fast path" rápido (health check, não reinstalação). Bootstrap pode estar reinstalando sem necessidade.`,
    );
  }

  console.log("[test] OK — bootstrap + doctor + fast path validados.");
}
