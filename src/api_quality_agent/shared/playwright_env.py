# Nomes de variável de ambiente compartilhados entre o lado que EMITE
# código (generators/playwright/default_playwright_test_suite_builder.py,
# que escreve isto no conftest.py gerado) e o lado que EXECUTA
# (adapters/playwright/playwright_adapter.py, que define o valor antes de
# subir o subprocess do pytest). Vive em shared/ (não em generators/ nem em
# adapters/) para não criar uma dependência de um pacote sobre o outro —
# nenhum dos dois hoje importa do outro, e isto não deveria mudar isso.
#
# P1.2: caminho de um arquivo NDJSON onde o api_context gerado registra
# cada transação HTTP, uma por linha — nunca uma credencial, só um caminho
# de arquivo temporário definido pelo PlaywrightAdapter.
HTTP_TRANSACTIONS_PATH_ENV_VAR = "PLAYWRIGHT_HTTP_TRANSACTIONS_PATH"

# P1.1 (detalhamento de assertions): caminho de um arquivo NDJSON onde CADA
# teste gerado registra o resultado de cada assertion realmente checada
# (name/expected/actual/status/precision/reason), uma por linha — emitido
# pelo helper _record_assertion_result embutido em cada arquivo de teste
# (playwright_endpoint_test_generator.py), nunca pelo conftest.py (esse
# helper é per-arquivo, no mesmo padrão de _assert_field_type/
# _assert_required_field_present, não uma fixture compartilhada).
ASSERTION_RESULTS_PATH_ENV_VAR = "PLAYWRIGHT_ASSERTION_RESULTS_PATH"

# P1.3 (Trace em falha): diretório onde a fixture api_context grava o
# arquivo .zip do Playwright Trace de um teste que FALHOU (nunca de um que
# passou — ver conftest.py gerado). Ausente = feature desligada (nenhuma
# suíte antiga, nem uma execução de pytest fora do PlaywrightAdapter,
# tenta gravar nada): a fixture só chama tracing.start()/stop() quando esta
# variável está definida.
TRACE_DIR_ENV_VAR = "PLAYWRIGHT_TRACE_DIR"

# P1.3: caminho de um arquivo NDJSON onde a fixture api_context registra a
# correlação test_id -> caminho do .zip bruto (ainda não mascarado) gravado
# em TRACE_DIR_ENV_VAR — uma linha só quando o teste falhou. Nunca o nome
# do arquivo sozinho como mecanismo de correlação (regra explícita do
# bloco): é este manifesto, lido pelo PlaywrightAdapter, que associa
# definitivamente test_id -> trace.
TRACE_ARTIFACTS_PATH_ENV_VAR = "PLAYWRIGHT_TRACE_ARTIFACTS_PATH"
