import * as fs from "fs";
import * as path from "path";
import { BootstrapManifest } from "./types";

/**
 * Lê o manifest.json embutido no .vsix (gerado por
 * scripts/prepare-release.ps1, nunca em runtime). Descreve exatamente
 * quais wheels foram empacotados nesta versão da extensão.
 */
export function readBundledManifest(extensionPath: string): BootstrapManifest {
  const manifestPath = path.join(extensionPath, "resources", "python-dist", "manifest.json");

  let raw: string;
  try {
    raw = fs.readFileSync(manifestPath, "utf8");
  } catch (error) {
    throw new Error(
      `Não foi possível ler '${manifestPath}'. Rode scripts/prepare-release.ps1 antes de empacotar/ativar a extensão. Detalhe: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }

  // Tolerante a um BOM (marca de byte-order) no início do arquivo — o
  // Node não remove isso sozinho ao ler com "utf8", e ferramentas como o
  // Set-Content do Windows PowerShell 5.1 escrevem BOM por padrão mesmo
  // pedindo -Encoding utf8 (bug real, já reproduzido e corrigido no
  // gerador em scripts/prepare-release.ps1 — isso aqui é defesa extra).
  const withoutBom = raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw;
  const parsed = JSON.parse(withoutBom);
  if (
    typeof parsed.packageVersion !== "string" ||
    typeof parsed.pythonTag !== "string" ||
    !Array.isArray(parsed.wheelFileNames) ||
    !parsed.wheelFileNames.every((name: unknown) => typeof name === "string")
  ) {
    throw new Error(`manifest.json em '${manifestPath}' está com formato inesperado.`);
  }

  return parsed as BootstrapManifest;
}
