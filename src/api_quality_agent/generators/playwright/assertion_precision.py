from enum import Enum


class AssertionPrecision(str, Enum):
    # Ver plano de ação Playwright, seção 10 ("Classificação das
    # asserções") — evita que uma asserção aproximada seja contabilizada
    # como validação exata, e sinaliza explicitamente o risco de falso
    # positivo.

    # Expectativa documentada explicitamente (ex.: response do OpenAPI,
    # teste Postman existente, contrato declarado).
    EXACT = "exact"
    # Expectativa derivada de evidência estruturada (ex.: OpenAPI define
    # 422 para erro de validação, mas não foi o dado usado diretamente).
    DERIVED = "derived"
    # Só uma expectativa aproximada (ex.: "4xx"), sem evidência para um
    # valor exato. Nunca deve ser contabilizada como validação exata —
    # todo uso desta precisão exige um warning associado.
    BROAD = "broad"
