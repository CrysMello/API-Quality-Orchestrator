from dataclasses import dataclass

from api_quality_agent.generators.playwright.assertion_precision import AssertionPrecision


@dataclass(frozen=True)
class AssertionClassification:
    # Parte 23 do plano de ação Playwright ("EXACT/DERIVED/BROAD") — o
    # registro estruturado que torna explícito o nível de confiança de UMA
    # expectativa realmente gerada no arquivo de teste (nunca de uma
    # expectativa que não chegou a existir: ausência de evidência continua
    # "nada gerado", não uma classificação). Reaproveita o
    # AssertionPrecision já existente desde a Parte 03 — nenhum modelo de
    # precisão duplicado.
    #
    # Nome estável da categoria de asserção — "status", "content_type",
    # "body", "required_fields", "field_types", "json_schema",
    # "expected_values" — nunca o texto livre de uma mensagem, para
    # permitir agregação confiável no generation-manifest.json (regra 4).
    assertion: str
    precision: AssertionPrecision
    # Origem da evidência — o mesmo AssertionOrigin.value já usado por
    # AssertionDefinition.origin (contract/example/configuration/context)
    # quando há uma AssertionDefinition por trás; "none" só no caso BROAD
    # por ausência total de evidência (Parte 16: nenhum status documentado).
    source: str
    # Texto curto explicando POR QUE esta precisão foi escolhida — nunca
    # "porque sim"; sempre aponta a evidência (EXACT), a regra de derivação
    # (DERIVED) ou a ausência que motivou a aproximação (BROAD).
    justification: str
