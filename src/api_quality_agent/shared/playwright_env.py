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
