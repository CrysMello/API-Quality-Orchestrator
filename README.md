# API Quality Orchestrator

Orquestrador de automação de qualidade para APIs, executado por linha de comando. Analisa contratos (JSON, OpenAPI/Swagger, Collections Postman), gera schemas e testes, e pode se conectar opcionalmente ao Postman para atualizar Collections de forma controlada.

A estrutura segue arquitetura hexagonal: o domínio (`domain/`) não depende de formatos externos (Postman, OpenAPI) nem de integrações — parsers e adapters ficam nas bordas. Consulte o Software Architecture Document (SAD) para detalhes de arquitetura, requisitos e roadmap.

## Estado atual

Já implementados (com testes automatizados — 984 testes, incluindo uma suíte de aceitação ponta a ponta em `tests/acceptance/`; mypy limpo):

- **CLI instalável**: `--help`, `--version`, `config show`, `doctor`, `version`, `workspace list`, `workspace select`, `list`, `generate`, `update`, `run` e `report`. Todos reutilizam os use cases já existentes (nenhuma regra de negócio nova na CLI): `workspace select`/`generate`/`update`/`run` aceitam seleção de Collection por ID, nome, índice ou interativamente (lógica compartilhada em `cli/collection_selection.py`), com Ctrl+C/EOF cancelando de forma limpa em qualquer prompt (tratamento centralizado em `cli/interactive.py`). `generate --file`/`run --file <collection.json>` rodam sem `POSTMAN_API_KEY` e sem nenhuma chamada de rede (ver "Modo Offline"). `generate --openapi-file <spec.json>` converte uma especificação OpenAPI/Swagger local numa Collection Postman sintética (`OpenApiCollectionConverter`) e roda o mesmo pipeline de geração sem alterá-lo — o resultado é uma Collection completa (`collection.json`, com os testes já embutidos), pronta para importar no Postman ou rodar direto com `run --file`. `generate --contract-file <contrato.xlsx>` usa um contrato de API declarado numa planilha Excel (schema por campo, não inferido de Examples) como fonte de schema para as requests da Collection que forem pareadas com ele (`ExcelContractParser` + `ExcelContractValidator` + `CanonicalEndpointNormalizer` + `ContractEndpointMatcher` + `SchemaProvider`), combinável com a seleção normal (online) ou com `--file`; endpoints sem contrato pareado continuam com a inferência de sempre (`FallbackSchemaProvider`), e um `contract-match-report.json`/`.html` é salvo junto dos demais artefatos mostrando o que casou, o que não foi encontrado e o que ficou ambíguo (nunca escolhido automaticamente). `update` gera os testes novamente a partir do estado *atual* da Collection no Postman e aplica a atualização remota (com preview, backup e confirmação padrão negativa) — reutiliza `GenerateCollectionTestsUseCase` + `UpdateCollectionUseCase` como já existiam, sem depender de artefatos de uma execução anterior do `generate`; só funciona com uma Collection real no Postman (não há modo offline para `update` — ver "Modo Offline"). `run` executa a Collection via Newman (`RunCollectionUseCase`), com o executável resolvido por `--newman-executable` > `NEWMAN_EXECUTABLE` > `"newman"`, mapeia o `ExecutionResult` para o código de saída (sucesso/falhas de teste/falha de infraestrutura são distinguidos, nunca por exceção), e persiste um resumo estruturado em `artifacts/run_<timestamp>/result.json` (schema `1.1`, aditivo sobre o `1.0` original — `PersistExecutionResultUseCase` + `JsonExecutionResultRepository`; nunca stdout/stderr brutos nem a Collection completa). `report` lê esse `result.json` (o mais recente por padrão, ou um `--input` específico) e gera um relatório HTML autocontido (`JsonExecutionResultReader` + `ReportEngine.render_execution_summary_html()` + `HtmlReportWriter`) — nunca reexecuta o Newman nem acessa o Postman; o código de saída representa se o relatório foi gerado, não se os testes passaram; funciona igual para resultados de execuções online e offline. Snapshots de contrato já existem e são testados na camada de aplicação, mas ainda não têm comando de CLI dedicado — ver limitações.
- **Entrada**: `InputResolver` (arquivo/stdin/conteúdo direto) e `JsonDocumentParser`.
- **Parsers de contrato**: OpenAPI 3.x / Swagger 2.0 (JSON ou YAML, com resolução de `$ref` interno) e Collection Postman (preserva scripts, pastas aninhadas e itens desconhecidos). `PostmanCollectionSerializer` faz o caminho inverso (documento → JSON do Postman), usado na atualização remota e no backup.
- **Normalização Postman**: `auth`/`body`/`url` convertidos em modelos tipados (`NormalizedAuth`, `NormalizedBody`, `NormalizedUrl`), sem expor segredos.
- **API Analysis Engine**: relaciona endpoints, parâmetros, autenticação e dependências prováveis entre requests (sempre com evidência, nunca por suposição), tanto para Collections Postman quanto para especificações OpenAPI (`analyze`/`analyze_specification`); também expõe cada request bruta pareada com sua análise (`analyze_collection_requests`), usado pelo orquestrador de geração.
- **Schema Inference Engine**: gera JSON Schema determinístico a partir de exemplos, com políticas conservadoras (`required` só com evidência, sem inferir formatos por nome de campo).
- **Test Strategy Engine**: converte a análise em uma estratégia de testes estruturada (asserções, extrações de variável, cenários negativos), cada decisão com origem rastreável.
- **Postman Test Generator**: converte a estratégia em JavaScript comentado para o campo *Tests* do Postman, com resumo legível em português e warnings de inferências incertas.
- **Integração com a API do Postman**: `PostmanApiClient` (via `urllib`, sem dependências de runtime novas), com leitura (`PostmanWorkspaceRepository`/`PostmanCollectionRepository`) e atualização (`update`, HTTP PUT), testados contra um servidor HTTP local (sem mocks).
- **Seleção de Workspace/Collection**: `CollectionSelectionService` (resolução por id/nome com precedência clara) e os use cases `SelectWorkspaceUseCase`/`SelectCollectionUseCase`/`ResolveCollectionUseCase`/`ClearWorkspaceUseCase`/`ClearCollectionUseCase`/`ListWorkspacesUseCase`/`ListCollectionsUseCase`. A seleção ativa é persistida localmente (`FileSelectionRepository`) guardando apenas `workspace_id`/`collection_id` — nunca API key ou nomes. Seleções temporárias (override por id/nome numa chamada pontual) nunca alteram a seleção ativa persistida.
- **Managed Block Merger**: mescla blocos gerados (`// <api-quality-agent:block id="...">...`) preservando código manual ao redor, com detecção de blocos duplicados/não fechados/corrompidos.
- **Diff Engine** e **Approval Policy**: comparam duas versões de uma Collection (requests, scripts, blocos gerenciados, variáveis) e decidem se uma atualização pode prosseguir (toda remoção é risco alto; `dry_run` sempre bloqueia).
- **Orquestração de geração** (`GenerateCollectionTestsUseCase` + `AgentOrchestrator`): para a Collection selecionada, executa o pipeline completo — parsing, análise, schema, estratégia, geração de scripts, merge em memória e diff — sem alterar a Collection original nem atualizar remotamente. Uma falha em um request específico não aborta os demais (resultado parcial, com contexto registrado). Os artefatos (scripts gerados e diff) são salvos isolados por `workspace_id`/`collection_id`/`execution_id` via `LocalArtifactRepository`.
- **Atualização remota segura** (`UpdateCollectionUseCase`): aplica em memória o resultado já aprovado via diff, atualizando *somente* a Collection selecionada (nunca por nome, nunca todas). Backup local da versão original antes de qualquer chamada remota (`LocalBackupRepository`): escrita atômica, nome único com timestamp e hash SHA-256, verificação de integridade, permissões restritivas de melhor esforço, retenção configurável e proteção contra versionamento acidental (`.gitignore`). O resultado devolvido carrega apenas metadados seguros (nunca o documento, o backup ou a resposta completa da API).
- **Newman Adapter + `RunCollectionUseCase`**: executa a Collection selecionada (ou um artefato local já gerado) via `subprocess` (sem shell, com timeout configurável), separando estruturalmente falhas de teste de falhas de infraestrutura (executável ausente, timeout, Collection inválida, erro inesperado). Segredos do arquivo de Environment do Postman (`"type": "secret"`) são mascarados na saída antes de entrarem no resultado.
- **Report Engine** (`reporting/`): monta um relatório estruturado a partir dos resultados já produzidos pelas etapas anteriores, com dois pontos de entrada — `generate()` (fluxo completo: execução, Workspace/Collection, endpoints, avisos, diff, atualização, execução do Newman, artefatos) e `generate_from_execution_summary()` (a partir de um `ExecutionResultRecord` lido de um `result.json` persistido pelo `run`, sem etapa de geração — seções de endpoints/diff/update ficam semanticamente vazias, nunca inventadas). Serialização em JSON (schema de topo estável), HTML genérico (`render_report_html`, com escaping) e um HTML dedicado ao relatório de execução (`render_execution_report_html`, com cards, barra de progresso e banner PASSED/FAILED/INFRASTRUCTURE FAILURE — texto sempre visível, nunca só cor) — nunca inclui corpo de requisição/resposta, headers ou blocos de autenticação brutos.
- **Snapshots de contrato** (`ContractSnapshot` + `ContractComparisonEngine`): persistem uma representação puramente estrutural (schema, status codes, content types — nunca valores reais) por Workspace/Collection/método/endpoint, e comparam duas versões de forma determinística (campo adicionado/removido, mudança de tipo/`required`/enum, status code, content type). Atualizar um baseline existente exige `overwrite=True` explícito.
- **Testes de aceitação ponta a ponta** (`tests/acceptance/`): validam os fluxos completos do SAD compondo os componentes reais acima (sem mocks internos — só um servidor Postman local simulado e um processo Newman simulado), incluindo alternância entre Collections, isolamento de artefatos e confirmação de que a atualização remota simulada nunca atinge uma Collection não selecionada. Matriz requisito×teste e limitações conhecidas do MVP em `tests/acceptance/README.md`.

Principais limitações atuais (detalhadas em `tests/acceptance/README.md`): não há comando de CLI para snapshots de contrato, já implementados e testados na camada de aplicação, mas hoje só acionáveis compondo as classes diretamente em Python; a geração de testes a partir de OpenAPI (`generate --openapi-file`) não mapeia `SecurityDefinition` para `auth` do Postman (configure via Environment depois de importar), não agrupa endpoints em pastas, só lê o campo `example` singular do OpenAPI 3 (não `examples`, plural — limitação do `OpenApiParser`), e nunca gera exemplo sintético a partir só de um `schema` sem `example` documentado (mesmo princípio de nunca inventar dado sem evidência real); **`update` não tem modo offline** — "atualizar" significa aplicar via PUT numa Collection real do Postman (o recibo de confirmação, com `status_code`/`request_id`, só existe numa chamada HTTP de verdade), então não há equivalente local sensato; `report` só gera HTML (`--format html` é a única opção nesta versão — Markdown/PDF/CSV ficam para depois) e só mostra contagens agregadas de falha (o `result.json` nunca guardou o detalhamento por request/teste, então o relatório não inventa uma tabela que não existe); snapshots de contrato ainda não estão conectados a nenhum fluxo de geração/atualização; `generate --contract-file` (schema declarado em planilha Excel) tem seu próprio conjunto de limitações de escopo — ver seção "Contrato declarado em planilha Excel"; sem relatório de cobertura de código configurado no projeto; sem lint automatizado configurado (só `mypy`).

## Instalação local

Requer Python 3.12 ou superior.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Como usar

### Instalação e configuração

Depois de instalado (`pip install -e ".[dev]"`), o executável `api-quality-orchestrator` fica disponível no ambiente (registrado via `[project.scripts]`). A API Key do Postman é lida da variável de ambiente `POSTMAN_API_KEY` — nunca de um arquivo, argumento de linha de comando ou de qualquer valor persistido em disco:

```bash
export POSTMAN_API_KEY="sua-chave-aqui"        # Windows (PowerShell): $env:POSTMAN_API_KEY = "sua-chave-aqui"
api-quality-orchestrator config show                  # confirma que a chave está configurada (mascarada)
api-quality-orchestrator doctor                       # verifica pré-requisitos locais
```

### Uso pela linha de comando

```bash
api-quality-orchestrator --help
api-quality-orchestrator --version
api-quality-orchestrator version

# Lista os Workspaces disponíveis para a API Key configurada
api-quality-orchestrator workspace list

# Seleciona o Workspace ativo, por ID, por nome, pelo índice mostrado por
# `workspace list`, ou interativamente (mesmas regras de generate, abaixo)
api-quality-orchestrator workspace select --workspace-id <id>
api-quality-orchestrator workspace select --workspace-name "Meu Workspace"
api-quality-orchestrator workspace select 1
api-quality-orchestrator workspace select

# Lista as Collections do Workspace ativo
api-quality-orchestrator list

# Gera e aplica os testes em uma Collection específica, por ID...
api-quality-orchestrator generate --collection-id 31333303-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# ...ou por nome (deve ser único no Workspace; se houver mais de uma Collection
# com esse nome, o comando lista os IDs e pede para usar --collection-id)...
api-quality-orchestrator generate --collection-name "Fake Store API Collection"

# ...ou pelo índice mostrado por `list`...
api-quality-orchestrator generate 2

# ...ou interativamente, escolhendo a partir da listagem exibida no terminal
api-quality-orchestrator generate

# Pula a confirmação final (útil em scripts/automação)
api-quality-orchestrator generate --collection-id <id> --yes

# Gera os testes a partir de uma Collection exportada localmente (Postman >
# Collection > Export), sem conectar à API do Postman e sem precisar de
# POSTMAN_API_KEY — útil quando você só tem o arquivo, ou quer gerar os
# scripts offline para colar manualmente depois
api-quality-orchestrator generate --file local/collections/minha_collection_exportada.json

# Gera uma Collection Postman completa (com os testes já embutidos) a partir
# de uma especificação OpenAPI/Swagger local — também sem POSTMAN_API_KEY
api-quality-orchestrator generate --openapi-file local/specs/minha_api.json

# Usa um contrato declarado numa planilha Excel como fonte de schema, em vez
# de inferir de Examples salvos — combinável com a seleção online normal...
api-quality-orchestrator generate --collection-id <id> --contract-file local/contratos/contrato.xlsx
# ...ou com --file (totalmente offline, sem POSTMAN_API_KEY)
api-quality-orchestrator generate --file local/collections/minha_collection_exportada.json --contract-file local/contratos/contrato.xlsx

# Depois de revisar os arquivos gerados em artifacts/.../scripts/, aplica a
# atualização na Collection remota do Postman (mesma seleção por ID/nome/
# índice/interativa de generate)
api-quality-orchestrator update --collection-id <id>
api-quality-orchestrator update -n "Fake Store API Collection"
api-quality-orchestrator update 2
api-quality-orchestrator update
api-quality-orchestrator update --collection-id <id> --yes

# Executa a Collection via Newman (mesma seleção por ID/nome/índice/interativa)
api-quality-orchestrator run --collection-id <id>
api-quality-orchestrator run -n "Fake Store API Collection"
api-quality-orchestrator run 2
api-quality-orchestrator run

# Configura o caminho do executável do Newman quando ele não está resolvível
# direto pelo PATH (comum no Windows — "newman" no PATH pode apontar para um
# .ps1 que o subprocess do Python não executa diretamente; use o .cmd)
api-quality-orchestrator run -c <id> --newman-executable "C:\Users\voce\AppData\Roaming\npm\newman.cmd"
# ...ou, se preferir configurar uma vez por sessão do terminal:
# $env:NEWMAN_EXECUTABLE="C:\Users\voce\AppData\Roaming\npm\newman.cmd"

# Executa uma Collection local via Newman, sem conectar à API do Postman
api-quality-orchestrator run --file local/collections/minha_collection_exportada.json

# Usa um Environment do Postman na execução (funciona em run e run --file)
api-quality-orchestrator run --collection-id <id> --environment local/environments/producao.json

# Gera um relatório HTML a partir do result.json mais recente em artifacts/
# (não precisa de POSTMAN_API_KEY nem executa o Newman de novo)
api-quality-orchestrator report

# ...ou de um result.json específico
api-quality-orchestrator report --input artifacts/run_20260720_103512123456/result.json

# Escolhe onde salvar (diretório ou caminho de arquivo)
api-quality-orchestrator report --output artifacts/reports
api-quality-orchestrator report --output meu_relatorio.html

# Substitui um relatório já existente (por padrão, report nunca sobrescreve)
api-quality-orchestrator report --overwrite
```

Em `workspace select`, `generate`, `update` e `run`, apenas uma forma de seleção pode ser usada por vez (ID, nome, índice ou, em `generate`/`run`, `--file`); combiná-las é rejeitado antes de qualquer chamada de rede. Sem `--yes` (em `generate`/`update`), sempre pedem confirmação antes de agir; qualquer resposta que não seja um "sim" reconhecido (incluindo Ctrl+C ou EOF, em qualquer prompt) cancela a operação sem alterar nada, com código de saída 9. Em `generate`/`update`/`run`, a seleção de Collection é sempre temporária (nunca sobrescreve a seleção ativa); já `workspace select`, ao ser confirmado, persiste o novo Workspace em `~/.api-quality-agent/selection.json` — e se o Workspace escolhido for diferente do anterior, a Collection ativa é limpa (pertencia ao contexto anterior).

`run` não pede confirmação (não é uma operação destrutiva) e nunca lança exceção para representar falha de teste ou de infraestrutura do Newman — ele sempre inspeciona o `ExecutionResult` devolvido por `RunCollectionUseCase` e mapeia para um código de saída: `0` (sucesso completo), `1` (Newman executou, mas houve falhas de assertion) ou `6` (falha de infraestrutura — executável não encontrado, timeout, Collection inválida; nesse caso nada é persistido, já que não há resumo real a salvar). O executável do Newman é resolvido nesta ordem: flag `--newman-executable` → variável de ambiente `NEWMAN_EXECUTABLE` → `"newman"` (padrão, via PATH). Um Environment do Postman pode ser informado via `-e/--environment <arquivo>` (em `run` e `run --file`); variáveis marcadas como `"type": "secret"` nesse arquivo já são mascaradas na saída do Newman antes de entrarem no resultado.

Ao final de uma execução com sucesso ou com falhas de teste, `run` grava um resumo estruturado em `artifacts/run_<timestamp>/result.json` e mostra o caminho:

```
Result saved to:
  artifacts/run_20260720_103512123456/result.json
```

O JSON só tem dados já expostos pelo domínio — nunca stdout/stderr brutos do Newman, nunca a Collection completa, nunca segredos:

```json
{
  "execution": {"started_at": "...", "finished_at": "...", "duration_seconds": 34.1},
  "collection": {"id": "...", "name": "PetStore"},
  "summary": {"requests": 28, "assertions": 312, "passed": 309, "failed": 3},
  "success": false,
  "infrastructure_failure": null
}
```

Se a gravação falhar (ex.: disco cheio), isso nunca muda o resultado da execução dos testes — só imprime um aviso à parte; o código de saída continua refletindo se os testes passaram ou não.

**`report` nunca executa o Newman de novo nem acessa o Postman — ele só lê um `result.json` já persistido pelo `run`.** Fluxo completo:

```
api-quality-orchestrator run     →  artifacts/run_<timestamp>/result.json
                           ↓
api-quality-orchestrator report  →  artifacts/run_<timestamp>/report.html
```

Sem `--input`, usa o `result.json` mais recente encontrado em `artifacts/**/result.json` (por data de modificação) e avisa qual escolheu ("Using latest execution result: ..."). Sem `--output`, o relatório fica ao lado do `result.json` de origem; `--output` aceita um diretório (nome do arquivo vira `report_<timestamp>.html`, reaproveitando o timestamp do próprio `result.json`) ou um caminho de arquivo completo. Por padrão `report` nunca sobrescreve um relatório existente — precisa de `--overwrite` explícito.

**O código de saída de `report` representa se o relatório foi gerado, não se os testes passaram.** Um `result.json` com testes falhos (`success: false`) ainda gera um relatório com exit code `0` — o status real (`PASSED`/`FAILED`/`INFRASTRUCTURE FAILURE`) aparece dentro do relatório, nunca no exit code. Únicas exceções: entrada inválida (arquivo inexistente, JSON corrompido, schema não suportado) usa os códigos de validação já existentes, e Ctrl+C sempre cancela com código 9.

O `result.json` schema `1.0` (sem `workspace`/`schema_version`) continua legível — `report` mostra "N/A" no lugar do Workspace nesse caso, em vez de falhar.

`generate --file`/`generate --openapi-file` rodam só a análise/geração local (sem `update` remoto — não há Collection do Postman pra atualizar); os artefatos são salvos em `artifacts/local/<nome-da-collection>/<execução>/`, junto com os demais. Como o export/a especificação pode conter tokens/headers salvos, evite versionar o arquivo — a pasta `local/` já é gitignored e serve bem pra isso.

**`generate` nunca altera a Collection remota — só `update` faz isso.** O fluxo recomendado é:

1. `api-quality-orchestrator generate -c <collection-id>` — gera os scripts em `artifacts/.../scripts/` para revisão local; nada muda no Postman.
2. Revise os arquivos `.js` gerados.
3. `api-quality-orchestrator update -c <collection-id>` — **gera os testes de novo**, a partir do estado *atual* da Collection no Postman (não lê os arquivos gerados no passo 1), mostra um preview (requests analisadas/alteradas/sem alteração, testes gerados, avisos), pede confirmação (padrão **negativo** — Enter vazio cancela, diferente de `generate`/`workspace select`), cria um backup local antes do upload e só então atualiza a Collection remota. Se a Collection tiver mudado entre os passos 1 e 3, o resultado do `update` reflete o estado novo — o preview do passo 1 pode não corresponder mais exatamente ao que será aplicado.

Os scripts de exemplo antigos em `local/` (ex.: `select_collection_and_generate.py`, com `COLLECTION_ID` editado manualmente no código) não são mais necessários — `api-quality-orchestrator workspace select` + `generate` + `update` os substituem.

### Modo Offline

Três dos quatro comandos que produzem/executam testes funcionam inteiramente com arquivos locais, sem `POSTMAN_API_KEY` e sem nenhuma chamada de rede:

```bash
api-quality-orchestrator generate --file collection.json         # gera os scripts a partir de uma Collection Postman
api-quality-orchestrator generate --openapi-file openapi.json    # gera Collection + scripts a partir de uma spec OpenAPI/Swagger
api-quality-orchestrator run --file collection.json              # executa a Collection via Newman
api-quality-orchestrator report                                  # gera o relatório (já é local por natureza)
```

`run --file` reaproveita o parâmetro `local_collection_path` que o `RunCollectionUseCase` já tinha internamente — nenhuma abstração nova, nenhuma duplicação da lógica de execução: o Newman roda direto sobre o arquivo informado, e o resultado é persistido em `result.json` normalmente (com `workspace`/`collection.id` como `null`, já que não há Workspace/Collection do Postman envolvidos — `report` já lida com isso mostrando "N/A"). `report` nunca dependeu do Postman para começo de conversa, então não precisou de nenhuma mudança — o mesmo comando lê `result.json` de execuções online e offline sem distinção.

`generate --openapi-file` não ensina o pipeline de geração (`SchemaInferenceEngine`/`TestStrategyEngine`/`PostmanTestGenerator`/`AgentOrchestrator`) a entender OpenAPI — em vez disso, `OpenApiCollectionConverter` converte a especificação numa `PostmanCollectionDocument` sintética (1 endpoint → 1 request; exemplos de request/response da spec viram exemplos salvos, o mesmo dado que `SchemaInferenceEngine` já consumia) e reaproveita o pipeline existente sem alterá-lo. O `collection.json` resultante já sai com os testes embutidos (`event`/`listen: "test"`) e pode ser importado direto no Postman ou executado com `run --file`.

**`update` não tem — e não terá — um modo offline.** "Atualizar" significa aplicar as mudanças via PUT numa Collection real do Postman; o retorno inclui um recibo de confirmação (`status_code`, `request_id`) que só existe numa chamada HTTP de verdade. Sem um destino remoto, não há o que "atualizar" — inventar esses campos pra uma operação puramente local violaria a mesma regra que o projeto segue desde o início (nunca inventar dado que não existe). Se um dia fizer sentido "aplicar os testes de volta no arquivo local", isso seria uma funcionalidade nova e diferente, não uma variação do `update` atual.

### Contrato declarado em planilha Excel (`--contract-file`)

Times que mantêm o contrato de cada endpoint numa planilha (campo, formato, tamanho, obrigatoriedade — schema **declarado**, não inferido de um Example salvo) podem usar esse contrato como fonte de schema para `generate`, em vez de depender de haver um "Example Response" salvo no Postman:

```bash
api-quality-orchestrator generate --collection-id <id> --contract-file contrato.xlsx
api-quality-orchestrator generate --file collection.json --contract-file contrato.xlsx   # totalmente offline
```

Como funciona, por dentro: `ExcelContractParser` lê a planilha (seções de Header/Path Param/Query Param/Body/Resposta por status HTTP, reconstruindo a árvore de objeto/array pela coluna `Sequencial`) e `ExcelContractValidator` verifica consistência (sequencial duplicado/órfão, tipo desconhecido, array sem filhos, path param não-obrigatório) — tudo sem conhecer a Collection. Cada endpoint declarado é pareado com uma request real da Collection por **método + path canônico** (`CanonicalEndpointNormalizer` + `ContractEndpointMatcher`): `{id}`, `:id` e `{{id}}` são tratados como equivalentes (o nome do parâmetro não importa, só a posição), e a variável de infraestrutura (`{{baseUrl}}`) nunca é confundida com parâmetro de path porque a canonização usa sempre o array `path` do Postman, nunca o `raw` completo.

Endpoints pareados usam o schema declarado; **endpoints sem contrato pareado continuam funcionando exatamente como antes** (inferência a partir de Examples salvos, via `FallbackSchemaProvider`) — `--contract-file` nunca faz uma Collection que já funcionava parar de gerar testes. Depois da geração, um relatório de correspondência é salvo junto dos demais artefatos (`artifacts/.../contracts/contract-match-report.json` e `.html`), mostrando o que casou, o que não foi encontrado e o que ficou ambíguo — ambiguidade **nunca** é resolvida escolhendo uma request automaticamente.

`ExcelContractValidator` roda automaticamente (sem flag) toda vez que `--contract-file` é usado, e seus diagnósticos (tipo desconhecido, sequencial duplicado/órfão, etc.) aparecem correlacionados no próprio relatório: entradas `MATCHED` carregam `validation_issues` da sua aba, entradas `AMBIGUOUS` carregam `candidate_validation_issues` separado por candidato, e a lista completa (inclusive diagnósticos de abas sem contrato utilizável) sempre aparece em `validation_issues` no nível raiz do JSON. Entradas `NOT_FOUND` **nunca** recebem correlação — não há nenhuma aba associada a um "não encontrado", então nenhuma causa é afirmada sem evidência determinística. Um contrato parcialmente inválido (ex.: um campo com tipo desconhecido) ainda entra no catálogo e ainda é pareado normalmente — `MATCHED` e `validation_issues` convivem na mesma entrada.

```bash
# Prefixo fixo de path presente só nas requests da Collection (ex.: de um
# gateway), ausente do path declarado no contrato — removido só por
# correspondência exata de segmentos no início do path
api-quality-orchestrator generate --collection-id <id> --contract-file contrato.xlsx --collection-path-prefix /api

# Falha o comando (exit code 1) se algum endpoint ficar sem contrato
# correspondente (UNMATCHED) ou com correspondência ambígua (AMBIGUOUS) —
# útil em CI; o relatório ainda é gerado e persistido antes da falha
api-quality-orchestrator generate --collection-id <id> --contract-file contrato.xlsx --strict-contract-match
```

**Escopo desta primeira versão** (decisões deliberadas, documentadas para transparência, não débito técnico):
- Só o schema da resposta de sucesso (HTTP 200) é usado — outras seções de status (400, 404, 500...) são reconhecidas pelo parser (não quebram a leitura) mas descartadas; sem lógica de seleção de schema por status recebido em runtime, sem asserção condicional em `pm.response.code`.
- Coluna `Tamanho` e `Regras (Domínio)` não são interpretadas (sem `maxLength`/`pattern`/etc.).
- `--strict-contract-match` falha só por `UNMATCHED`/`AMBIGUOUS` — diagnósticos de validação nunca alteram o exit code.
- Sem suporte a múltiplos contratos declarados na mesma aba (uma aba = no máximo um contrato) nem correlação por faixa de linhas.
- `generate --contract-file` nunca atualiza a Collection remota — combina normalmente com o fluxo `update` já existente (revisão manual antes de aplicar).

### Gestão de artefatos

Cada execução de `generate` (ou `run`) cria uma pasta própria, identificada por um `execution_id` único, sem sobrescrever execuções anteriores — isso é proposital, pra permitir comparar resultados antigos com novos.

**Atenção**: isso significa que `artifacts/` cresce a cada execução e **não há limpeza automática**. Em uso frequente (ex.: rodando `generate` em CI ou a cada iteração local), o diretório pode acumular bastante conteúdo ao longo do tempo e ocupar espaço em disco sem aviso.

A limpeza é responsabilidade do usuário — apague manualmente as pastas de execuções que não precisa mais manter. Não existe (ainda) um comando de retenção/limpeza automática.

### Fluxos ainda sem comando de CLI (disponíveis via Python)

O relatório completo do pipeline de geração (`ReportEngine.generate()`, com endpoints/diff/atualização/execução do Newman — diferente do relatório de execução isolado que `report` já expõe) e snapshots de contrato já estão implementados e testados (ver "Estado atual"), mas hoje só são acionáveis compondo as classes diretamente em Python — não há comando de CLI para eles. Exemplo mínimo (selecionar Workspace/Collection e gerar testes — equivalente ao que `workspace select` + `generate` já fazem pela CLI):

```python
import os

from api_quality_agent.adapters.config import FileSelectionRepository
from api_quality_agent.adapters.filesystem import LocalArtifactRepository
from api_quality_agent.adapters.postman import (
    PostmanApiClient,
    PostmanCollectionRepository,
    PostmanWorkspaceRepository,
)
from api_quality_agent.application.orchestration import AgentOrchestrator
from api_quality_agent.application.use_cases import (
    GenerateCollectionTestsUseCase,
    GetCurrentWorkspaceUseCase,
    ResolveCollectionUseCase,
    SelectCollectionUseCase,
    SelectWorkspaceUseCase,
)
from api_quality_agent.domain.services import (
    ApiAnalysisEngine,
    CollectionSelectionService,
    DiffEngine,
    ManagedBlockMerger,
    SchemaInferenceEngine,
    TestStrategyEngine,
)
from api_quality_agent.generators import PostmanTestGenerator

client = PostmanApiClient(os.environ["POSTMAN_API_KEY"])
workspace_repository = PostmanWorkspaceRepository(client)
collection_repository = PostmanCollectionRepository(client)
selection_repository = FileSelectionRepository()  # persiste em ~/.api-quality-agent/selection.json
selection_service = CollectionSelectionService(collection_repository)

SelectWorkspaceUseCase(workspace_repository, selection_repository).execute(workspace_name="Meu Workspace")
SelectCollectionUseCase(selection_service, selection_repository).execute(collection_name="Minha Collection")

orchestrator = AgentOrchestrator(
    ApiAnalysisEngine(), SchemaInferenceEngine(), TestStrategyEngine(),
    PostmanTestGenerator(), ManagedBlockMerger(), DiffEngine(),
)
generate = GenerateCollectionTestsUseCase(
    GetCurrentWorkspaceUseCase(selection_repository),
    ResolveCollectionUseCase(selection_service, collection_repository, selection_repository),
    collection_repository, orchestrator, LocalArtifactRepository(),
)
result = generate.execute()
print(f"{len(result.endpoint_outcomes)} endpoints processados; diff com mudanças: {result.diff.has_changes}")
```

A mesma composição, estendida com atualização remota (`UpdateCollectionUseCase`), execução via Newman (`RunCollectionUseCase` + `NewmanAdapter`) e relatório (`ReportEngine`), está em `tests/acceptance/conftest.py::build_app` — é a montagem de referência, validada pela suíte de aceitação. `tests/acceptance/README.md` documenta a jornada completa (seleção → geração → atualização → execução → relatório) passo a passo.

## Limitações conhecidas

- **Sem cobertura de casos de erro** (só happy path) — testes negativos
  ainda não são gerados automaticamente.
- **Limpeza de artefatos é manual** — cada execução cria uma pasta nova em
  `artifacts\`, sem retenção automática.
- **Schema inferido a partir de exemplo(s), não de contrato formal** — os
  testes de tipo/schema são deduzidos a partir do(s) "Example Response"
  salvo(s) (Postman) ou `example`/`examples` documentado(s) (OpenAPI), nunca
  inventados sem evidência. Isso significa que variações legítimas da API
  não representadas nos exemplos disponíveis (campo opcional ausente,
  `null`, tipos mistos em array) podem gerar um teste que falha numa
  resposta correta. Não é um bug a ser corrigido, mas um trade-off inerente
  a qualquer geração baseada em exemplo — usar mais exemplos reduz o risco,
  mas não o elimina.

## Comandos de qualidade

```bash
# Executar testes
pytest

# Verificação de tipos
mypy src
```

## Licença

Este projeto é distribuído sob os termos da licença MIT.

Consulte o arquivo [LICENSE](LICENSE) para mais informações.

## Author

Projeto idealizado, arquitetado e mantido por Crystiane Mello.

Este projeto foi desenvolvido utilizando uma abordagem de Human-directed AI development. Ferramentas de Inteligência Artificial foram utilizadas como apoio à análise, documentação e implementação, enquanto a concepção, as decisões de arquitetura, o direcionamento, a revisão, a validação e a manutenção permaneceram sob responsabilidade da autora.

- **Nome**: Crystiane Mello
- **Papéis**: Creator, Architect, Maintainer
- **Abordagem de desenvolvimento**: Human-directed AI development
- **GitHub**: [https://github.com/CrysMello](https://github.com/CrysMello)
- **LinkedIn**: [https://www.linkedin.com/in/crystianemello/](https://www.linkedin.com/in/crystianemello/)
