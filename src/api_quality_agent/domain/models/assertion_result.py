from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssertionResult:
    # Evidência de UMA assertion realmente checada durante a execução real
    # da suíte Playwright (P1.1 — detalhamento de assertions) — nunca
    # inventada: só existe quando o código gerado (playwright_endpoint_test_
    # generator.py) de fato embutiu um `assert` para ela, na MESMA
    # categoria/precisão já classificada em tempo de geração
    # (AssertionClassification, generators/playwright/). PlaywrightAdapter
    # nunca decide sozinho o que é secret aqui — mascara expected/actual/
    # reason com a mesma known_secret_values já usada pra stdout/stderr/
    # transações HTTP.
    #
    # test_id: mesma chave usada em HttpTransaction.test_id — é o que
    # permite reconstruir test_id -> request -> response -> assertions.
    test_id: str
    # Nome estável da assertion (ex.: "HTTP status", "required_field:user.id",
    # "json_schema") — nunca uma mensagem de erro solta.
    name: str
    # expected sempre tem origem rastreável (contrato/schema/TestStrategy —
    # ver AssertionDefinition.origin/AssertionClassification.source, nunca
    # um valor inventado na hora de gerar ou de ler o resultado).
    expected: Any
    # actual vem sempre da resposta realmente executada (response.status,
    # presença real do campo, resultado real da validação de schema).
    actual: Any
    # "PASSED" | "FAILED" — nunca um terceiro estado; decidido pelo MESMO
    # `assert` que já existia no arquivo gerado (nunca uma segunda
    # comparação paralela).
    status: str
    # "EXACT" | "DERIVED" | "BROAD" — reaproveita
    # generators.playwright.AssertionPrecision.value.upper(), nunca uma
    # classificação nova.
    precision: str
    # Por que consideramos isto correto — sempre derivado de
    # AssertionClassification.justification (mesma fonte da docstring do
    # teste gerado), nunca "porque sim".
    reason: str
