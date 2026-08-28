import re
from typing import Any

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionOrigin,
    AssertionType,
    EndpointAnalysis,
    NegativeCaseDefinition,
    NegativeCaseType,
    StrategyWarning,
    TestStrategy,
    TestStrategyOptions,
    VariableExtraction,
    VariableScope,
)

_ID_FIELD_PATTERN = re.compile(r"(^id$|.*[_-]id$)", re.IGNORECASE)
_LIMIT_KEYS = frozenset(
    {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"}
)
_NO_CONTENT_STATUS_CODES = frozenset({204})

# Tipos de asserção que devem aparecer no máximo uma vez por estratégia.
# REQUIRED_FIELD_PRESENT é intencionalmente multi-instância (uma por campo
# obrigatório) e não entra nesta checagem.
_SINGULAR_ASSERTION_TYPES = frozenset(
    {
        AssertionType.STATUS_CODE,
        AssertionType.CONTENT_TYPE,
        AssertionType.RESPONSE_TIME,
        AssertionType.VALID_JSON_BODY,
        AssertionType.SCHEMA,
        AssertionType.ARRAY_NOT_EMPTY,
        AssertionType.NO_EXTRA_PROPERTIES,
        AssertionType.SNAPSHOT,
    }
)


def _is_json_content_type(content_type: str) -> bool:
    # Ignora parâmetros do cabeçalho (ex.: "; charset=utf-8"), comuns em APIs
    # reais — sem isso, "application/json; charset=utf-8" nunca era
    # reconhecido como JSON e todas as asserções de corpo/schema eram
    # puladas silenciosamente.
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


class TestStrategyEngine:
    def build_strategy(
        self,
        endpoint: EndpointAnalysis,
        *,
        response_schema: dict[str, Any] | None = None,
        request_schema: dict[str, Any] | None = None,
        options: TestStrategyOptions | None = None,
        expected_status_code: int | None = None,
    ) -> TestStrategy:
        effective_options = options or TestStrategyOptions()
        warnings: list[StrategyWarning] = []
        assertions: list[AssertionDefinition] = []

        status_code, status_origin = _determine_expected_status(
            endpoint, effective_options, expected_status_code, warnings
        )
        if status_code is not None and status_origin is not None:
            assertions.append(
                AssertionDefinition(
                    assertion_type=AssertionType.STATUS_CODE,
                    description=f"Status code da resposta deve ser {status_code}.",
                    expected_value=status_code,
                    origin=status_origin.value,
                )
            )

        if endpoint.response_content_types:
            content_type = endpoint.response_content_types[0]
            assertions.append(
                AssertionDefinition(
                    assertion_type=AssertionType.CONTENT_TYPE,
                    description=f"Content-Type da resposta deve conter '{content_type}'.",
                    expected_value=content_type,
                    origin=AssertionOrigin.CONTRACT.value,
                )
            )

        if effective_options.max_response_time_ms is not None:
            assertions.append(
                AssertionDefinition(
                    assertion_type=AssertionType.RESPONSE_TIME,
                    description=(
                        f"Tempo de resposta deve ser menor que "
                        f"{effective_options.max_response_time_ms}ms."
                    ),
                    expected_value=effective_options.max_response_time_ms,
                    origin=AssertionOrigin.CONFIGURATION.value,
                )
            )

        has_body = status_code not in _NO_CONTENT_STATUS_CODES
        has_json_content = has_body and any(
            _is_json_content_type(ct) for ct in endpoint.response_content_types
        )

        if has_json_content:
            assertions.append(
                AssertionDefinition(
                    assertion_type=AssertionType.VALID_JSON_BODY,
                    description="O corpo da resposta deve ser um JSON válido.",
                    expected_value=None,
                    origin=AssertionOrigin.CONTRACT.value,
                )
            )

            if response_schema is not None:
                assertions.extend(
                    _build_schema_related_assertions(response_schema, effective_options)
                )

        if effective_options.enable_snapshot:
            assertions.append(
                AssertionDefinition(
                    assertion_type=AssertionType.SNAPSHOT,
                    description="A resposta deve corresponder ao snapshot salvo anteriormente.",
                    expected_value=None,
                    origin=AssertionOrigin.CONFIGURATION.value,
                )
            )

        variable_extractions = (
            _find_variable_extraction_candidates(response_schema, endpoint.variables_defined)
            if has_json_content and response_schema is not None
            else ()
        )

        negative_cases = (
            _generate_negative_cases(request_schema) if request_schema is not None else ()
        )

        _check_contradictions(assertions, warnings, endpoint.source)

        return TestStrategy(
            endpoint_source=endpoint.source,
            assertions=tuple(assertions),
            variable_extractions=variable_extractions,
            negative_cases=negative_cases,
            warnings=tuple(warnings),
        )


def _determine_expected_status(
    endpoint: EndpointAnalysis,
    options: TestStrategyOptions,
    context_status: int | None,
    warnings: list[StrategyWarning],
) -> tuple[int | None, AssertionOrigin | None]:
    if context_status is not None:
        return context_status, AssertionOrigin.CONTEXT

    if options.expected_status_code is not None:
        return options.expected_status_code, AssertionOrigin.CONFIGURATION

    success_codes = [code for code in endpoint.response_status_codes if code.startswith("2")]
    if success_codes:
        return int(success_codes[0]), AssertionOrigin.CONTRACT

    if endpoint.response_status_codes:
        return int(endpoint.response_status_codes[0]), AssertionOrigin.CONTRACT

    warnings.append(
        StrategyWarning(
            code="STATUS_CODE_AMBIGUOUS",
            message=(
                "Nenhum status code documentado, configurado ou informado pelo contexto; "
                "não é possível determinar o status esperado sem inventar um valor."
            ),
            endpoint=endpoint.source,
        )
    )
    return None, None


def _build_schema_related_assertions(
    response_schema: dict[str, Any], options: TestStrategyOptions
) -> list[AssertionDefinition]:
    assertions = [
        AssertionDefinition(
            assertion_type=AssertionType.SCHEMA,
            description="O corpo da resposta deve validar contra o schema esperado.",
            expected_value=response_schema,
            origin=AssertionOrigin.CONTRACT.value,
        )
    ]

    schema_type = response_schema.get("type")

    if options.assert_array_not_empty and schema_type == "array":
        assertions.append(
            AssertionDefinition(
                assertion_type=AssertionType.ARRAY_NOT_EMPTY,
                description="O array retornado não deve estar vazio.",
                expected_value=True,
                origin=AssertionOrigin.CONFIGURATION.value,
            )
        )

    if schema_type == "object":
        if options.assert_no_extra_properties:
            assertions.append(
                AssertionDefinition(
                    assertion_type=AssertionType.NO_EXTRA_PROPERTIES,
                    description=(
                        "A resposta não deve conter propriedades além das declaradas no schema."
                    ),
                    expected_value=True,
                    origin=AssertionOrigin.CONFIGURATION.value,
                )
            )

        for field_name in response_schema.get("required", []):
            assertions.append(
                AssertionDefinition(
                    assertion_type=AssertionType.REQUIRED_FIELD_PRESENT,
                    description=f"O campo obrigatório '{field_name}' deve estar presente na resposta.",
                    expected_value={
                        "field": field_name,
                        "must_have_value": options.assert_required_has_value,
                    },
                    origin=AssertionOrigin.CONTRACT.value,
                )
            )

    return assertions


def _find_variable_extraction_candidates(
    response_schema: dict[str, Any],
    defined_variables: tuple[str, ...] = (),
) -> tuple[VariableExtraction, ...]:
    if response_schema.get("type") != "object":
        return ()

    properties = response_schema.get("properties", {})
    candidate_fields = [
        field_name
        for field_name, field_schema in properties.items()
        if isinstance(field_schema, dict)
        and _ID_FIELD_PATTERN.match(field_name)
        and field_schema.get("type") in ("string", "integer")
    ]

    # Nome semântico explícito (P3.3): quando a PRÓPRIA Collection já diz,
    # por um script de teste real (pm.collectionVariables.set(...)/
    # pm.environment.set(...)/etc., ver api_analysis_engine.
    # _extract_defined_variables), que nome de negócio foi produzido —
    # NUNCA comparado textualmente ao nome do campo da resposta (isso
    # seria o matching heurístico proibido nesta parte: "id" e
    # "customer_id" nunca são considerados "parecidos"). Só aplicado sem
    # NENHUMA ambiguidade: exatamente um campo candidato na resposta E
    # exatamente um nome definido pelo script desta MESMA request —
    # qualquer outra combinação (nenhum script, mais de um nome definido,
    # mais de um campo candidato) preserva o comportamento de sempre
    # (variable_name = nome literal do campo), nunca uma adivinhação.
    semantic_name = (
        defined_variables[0]
        if len(candidate_fields) == 1 and len(defined_variables) == 1
        else None
    )

    return tuple(
        VariableExtraction(
            variable_name=semantic_name or field_name,
            source="response.body",
            json_path=f"$.{field_name}",
            scope=VariableScope.COLLECTION,
            origin=AssertionOrigin.CONTRACT.value,
        )
        for field_name in candidate_fields
    )


def _generate_negative_cases(
    request_schema: dict[str, Any],
) -> tuple[NegativeCaseDefinition, ...]:
    if request_schema.get("type") != "object":
        return ()

    properties = request_schema.get("properties", {})
    required_fields = request_schema.get("required", [])
    cases: list[NegativeCaseDefinition] = []

    for field_name in required_fields:
        cases.append(
            NegativeCaseDefinition(
                case_type=NegativeCaseType.MISSING_REQUIRED_FIELD,
                field=field_name,
                description=(
                    f"Omitir o campo obrigatório '{field_name}' e esperar erro de validação."
                ),
                evidence=f"'{field_name}' está listado em 'required' no schema do request.",
            )
        )

    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue

        if "enum" in field_schema:
            cases.append(
                NegativeCaseDefinition(
                    case_type=NegativeCaseType.INVALID_ENUM_VALUE,
                    field=field_name,
                    description=(
                        f"Enviar um valor fora do conjunto permitido para '{field_name}' "
                        "e esperar erro de validação."
                    ),
                    evidence=f"'{field_name}' declara 'enum' no schema do request.",
                )
            )

        field_type = field_schema.get("type")
        if isinstance(field_type, str) and field_type not in ("object", "array"):
            cases.append(
                NegativeCaseDefinition(
                    case_type=NegativeCaseType.INVALID_TYPE,
                    field=field_name,
                    description=(
                        f"Enviar um valor de tipo incorreto para '{field_name}' "
                        f"(esperado: {field_type}) e esperar erro de validação."
                    ),
                    evidence=f"'{field_name}' declara type='{field_type}' no schema do request.",
                )
            )

        present_limits = sorted(_LIMIT_KEYS & field_schema.keys())
        if present_limits:
            cases.append(
                NegativeCaseDefinition(
                    case_type=NegativeCaseType.LIMIT_VIOLATION,
                    field=field_name,
                    description=(
                        f"Enviar um valor fora do limite declarado para '{field_name}' "
                        "e esperar erro de validação."
                    ),
                    evidence=f"'{field_name}' declara limite(s) {present_limits} no schema do request.",
                )
            )

    return tuple(cases)


def _check_contradictions(
    assertions: list[AssertionDefinition],
    warnings: list[StrategyWarning],
    endpoint_source: str,
) -> None:
    seen_by_type: dict[AssertionType, Any] = {}
    for assertion in assertions:
        if assertion.assertion_type not in _SINGULAR_ASSERTION_TYPES:
            continue
        if assertion.assertion_type not in seen_by_type:
            seen_by_type[assertion.assertion_type] = assertion.expected_value
            continue
        if seen_by_type[assertion.assertion_type] != assertion.expected_value:
            warnings.append(
                StrategyWarning(
                    code="CONTRADICTORY_ASSERTION",
                    message=(
                        f"Mais de uma asserção '{assertion.assertion_type.value}' com valores "
                        "diferentes foi gerada para o mesmo endpoint."
                    ),
                    endpoint=endpoint_source,
                )
            )
