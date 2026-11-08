# API Quality Orchestrator — Extensão VSCode

Fase 1 (MVP): expõe os comandos determinísticos do CLI Python
`api-quality-orchestrator` (projeto irmão, na raiz deste repositório) na
Paleta de Comandos do VSCode,
sem depender de Copilot, LLM ou token nenhum. O `.whl` do projeto é embutido
no `.vsix` e instalado automaticamente num ambiente Python isolado
(`globalStorage` da extensão) na primeira ativação — o usuário não precisa
rodar `pip install` nem ter internet nesse momento (só precisa de Python
3.12+ instalado no sistema).

Uso pessoal/privado por enquanto — não publicada na Marketplace.

## Estado atual (Fase 1, em construção incremental)

Comandos planejados: `Doctor`, `Version`, `Config: Show`, `Generate`
(offline, a partir de Collection Postman `.json` ou spec OpenAPI/Swagger
`.json`), `Run` (offline, Postman via Newman ou Playwright via pytest),
`Report` (abre o relatório HTML gerado no navegador padrão).

Fora de escopo desta fase (ver plano de implementação): `update` (só
online, destrutivo), seleção online de Workspace/Collection (exige
`POSTMAN_API_KEY`), e o chat participant `@apiquality` (Fase 2).

## Desenvolvimento

Pré-requisito: Node 18+ e Python 3.12+ instalados no sistema.

```powershell
cd extension
npm install
npm run compile
```

Abra esta pasta (`extension/`) diretamente no VSCode — **não** a raiz do
repositório — e pressione F5 para abrir uma janela de Extension Development
Host com a extensão carregada.

## Empacotar uma release local

```powershell
./scripts/prepare-release.ps1   # gera os wheels em resources/python-dist/
npm install
npm run compile
npx @vscode/vsce ls              # confirma o que vai entrar no .vsix
npx @vscode/vsce package --allow-missing-repository
```

Instalar o `.vsix` gerado: `code --install-extension api-quality-orchestrator-<versão>.vsix`.
