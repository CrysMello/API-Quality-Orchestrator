# Testes de caracterização — linha de base pré-Playwright

Estes testes não validam requisitos novos. Eles **fotografam o comportamento
atual** (Postman/Newman) do API Quality Orchestrator antes do início da
implementação do suporte a Playwright (ver plano em
`C:\Users\Crys\.claude\plans\woolly-toasting-riddle.md`), para que qualquer
regressão introduzida durante essa implementação seja detectada de imediato
— mesmo quando os testes já existentes em `tests/acceptance` e `tests/unit`
(que verificam propriedades/estrutura, não o texto exato) não seriam
sensíveis o bastante para pegar uma mudança sutil.

Cada teste aqui compara uma saída real e atual do sistema contra um
"golden file" (`golden/`) capturado literalmente do código em
2026-08-04, execução por execução — não contra uma expectativa escrita à
mão.

## O que está coberto

- `test_generate_postman_golden.py` — roda `generate --file` (offline, sem
  rede) sobre a fixture `tests/acceptance/fixtures/offline_collection.json`
  e compara, byte a byte: os scripts JS gerados por endpoint, o
  `diff.json`, e a estrutura de diretórios (`{workspace_id}/{collection_id}/
  {execution_id}/{category}/{relative_path}`) produzida pelo
  `LocalArtifactRepository`.
- `test_execution_result_schema.py` — trava `EXECUTION_RESULT_SCHEMA_VERSION
  == "1.2"` e o conjunto exato de chaves serializadas em `result.json`
  (`PersistExecutionResultUseCase._serialize`). A Fase 9 do plano Playwright
  vai bumpar para `1.3` deliberadamente — quando isso acontecer, este teste
  precisa ser atualizado conscientemente, não quebrar como efeito colateral
  de outra mudança.
- `test_report_engine_source_hardcoded.py` — documenta que
  `ReportEngine.generate_from_execution_summary` hoje grava `source="newman"`
  fixo (o ponto que a Fase 9 do plano Playwright torna dinâmico). Serve como
  registro explícito do estado "antes".
- `test_cli_commands_inventory.py` — trava o conjunto atual de subcomandos
  registrados em `cli.main.build_parser()` e as flags centrais de
  `generate`/`run`/`report`. A Fase 2 do plano (`--target`) vai alterar esse
  inventário deliberadamente.

## Como rodar

```bash
pytest tests/characterization -v
```

## Quando um teste destes quebrar

Se a mudança foi **intencional** (ex.: Fase 2 adicionou `--target`, Fase 9
bumpou o schema): atualize o golden file / a asserção nesta suíte para
refletir o novo estado esperado, e diga isso explicitamente no commit — não
é uma regressão, é a suíte fazendo seu trabalho de exigir uma decisão
consciente.

Se a mudança **não** era esperada pela fase em andamento: é uma regressão
real — pare e investigue antes de continuar.
