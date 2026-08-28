from api_quality_agent.domain.models import (
    AssertionType,
    EndpointAnalysis,
    NegativeCaseType,
    TestStrategyOptions,
)
from api_quality_agent.domain.services import TestStrategyEngine


def _build_endpoint(
    *,
    source="GET /pets",
    method="GET",
    path="/pets",
    response_status_codes=(),
    response_content_types=(),
    variables_defined=(),
) -> EndpointAnalysis:
    return EndpointAnalysis(
        source=source,
        method=method,
        path=path,
        operation_id=None,
        parameters=(),
        has_request_body=False,
        request_content_types=(),
        response_status_codes=response_status_codes,
        response_content_types=response_content_types,
        auth_type=None,
        variables_used=(),
        has_examples=False,
        example_count=0,
        variables_defined=variables_defined,
    )


def _assertion_of_type(strategy, assertion_type):
    return next(a for a in strategy.assertions if a.assertion_type == assertion_type)


# --- GET 200 / POST 201 -------------------------------------------------------


def test_get_endpoint_with_documented_200_status():
    endpoint = _build_endpoint(
        source="GET /pets",
        method="GET",
        response_status_codes=("200",),
        response_content_types=("application/json",),
    )

    strategy = TestStrategyEngine().build_strategy(endpoint)

    status_assertion = _assertion_of_type(strategy, AssertionType.STATUS_CODE)
    assert status_assertion.expected_value == 200
    assert status_assertion.origin == "contract"


def test_post_endpoint_with_documented_201_status():
    endpoint = _build_endpoint(
        source="POST /pets",
        method="POST",
        response_status_codes=("201",),
        response_content_types=("application/json",),
    )

    strategy = TestStrategyEngine().build_strategy(endpoint)

    status_assertion = _assertion_of_type(strategy, AssertionType.STATUS_CODE)
    assert status_assertion.expected_value == 201


def test_status_never_defaults_to_200_without_evidence():
    endpoint = _build_endpoint(source="DELETE /pets/1", method="DELETE", response_status_codes=("204",))

    strategy = TestStrategyEngine().build_strategy(endpoint)

    status_assertion = _assertion_of_type(strategy, AssertionType.STATUS_CODE)
    assert status_assertion.expected_value == 204


# --- Response sem body ---------------------------------------------------------


def test_no_content_response_generates_no_body_related_assertions():
    endpoint = _build_endpoint(
        source="DELETE /pets/1", method="DELETE", response_status_codes=("204",)
    )
    response_schema = {"type": "object", "properties": {}}

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    assertion_types = {a.assertion_type for a in strategy.assertions}
    assert AssertionType.VALID_JSON_BODY not in assertion_types
    assert AssertionType.SCHEMA not in assertion_types
    assert strategy.variable_extractions == ()


def test_response_without_content_type_does_not_generate_json_body_assertion():
    endpoint = _build_endpoint(
        source="GET /pets", method="GET", response_status_codes=("200",), response_content_types=()
    )

    strategy = TestStrategyEngine().build_strategy(endpoint)

    assertion_types = {a.assertion_type for a in strategy.assertions}
    assert AssertionType.VALID_JSON_BODY not in assertion_types
    assert AssertionType.CONTENT_TYPE not in assertion_types


# --- Schema --------------------------------------------------------------------


def test_schema_assertion_uses_provided_schema():
    endpoint = _build_endpoint(
        response_status_codes=("200",), response_content_types=("application/json",)
    )
    response_schema = {"type": "object", "properties": {"id": {"type": "integer"}}}

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    schema_assertion = _assertion_of_type(strategy, AssertionType.SCHEMA)
    assert schema_assertion.expected_value == response_schema
    assert schema_assertion.origin == "contract"


def test_schema_assertion_is_generated_when_content_type_has_charset_parameter():
    # Regressão: "application/json; charset=utf-8" (comum em APIs reais) não
    # era reconhecido como JSON, então schema/corpo eram pulados por engano.
    endpoint = _build_endpoint(
        response_status_codes=("200",),
        response_content_types=("application/json; charset=utf-8",),
    )
    response_schema = {"type": "object", "properties": {"id": {"type": "integer"}}}

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    assertion_types = {a.assertion_type for a in strategy.assertions}
    assert AssertionType.VALID_JSON_BODY in assertion_types
    schema_assertion = _assertion_of_type(strategy, AssertionType.SCHEMA)
    assert schema_assertion.expected_value == response_schema


def test_no_schema_assertion_when_schema_not_provided():
    endpoint = _build_endpoint(
        response_status_codes=("200",), response_content_types=("application/json",)
    )

    strategy = TestStrategyEngine().build_strategy(endpoint)

    assertion_types = {a.assertion_type for a in strategy.assertions}
    assert AssertionType.SCHEMA not in assertion_types
    assert AssertionType.VALID_JSON_BODY in assertion_types


# --- Array não vazio habilitado/desabilitado -----------------------------------


def test_array_not_empty_assertion_enabled():
    endpoint = _build_endpoint(
        response_status_codes=("200",), response_content_types=("application/json",)
    )
    array_schema = {"type": "array", "items": {"type": "object"}}

    strategy = TestStrategyEngine().build_strategy(
        endpoint,
        response_schema=array_schema,
        options=TestStrategyOptions(assert_array_not_empty=True),
    )

    assertion_types = {a.assertion_type for a in strategy.assertions}
    assert AssertionType.ARRAY_NOT_EMPTY in assertion_types


def test_array_not_empty_assertion_disabled_by_default():
    endpoint = _build_endpoint(
        response_status_codes=("200",), response_content_types=("application/json",)
    )
    array_schema = {"type": "array", "items": {"type": "object"}}

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=array_schema)

    assertion_types = {a.assertion_type for a in strategy.assertions}
    assert AssertionType.ARRAY_NOT_EMPTY not in assertion_types


def test_array_not_empty_assertion_not_generated_for_object_schema():
    endpoint = _build_endpoint(
        response_status_codes=("200",), response_content_types=("application/json",)
    )
    object_schema = {"type": "object", "properties": {}}

    strategy = TestStrategyEngine().build_strategy(
        endpoint,
        response_schema=object_schema,
        options=TestStrategyOptions(assert_array_not_empty=True),
    )

    assertion_types = {a.assertion_type for a in strategy.assertions}
    assert AssertionType.ARRAY_NOT_EMPTY not in assertion_types


# --- Extração de id --------------------------------------------------------------


def test_id_field_generates_variable_extraction():
    endpoint = _build_endpoint(
        response_status_codes=("201",), response_content_types=("application/json",)
    )
    response_schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
    }

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    assert len(strategy.variable_extractions) == 1
    extraction = strategy.variable_extractions[0]
    assert extraction.variable_name == "id"
    assert extraction.json_path == "$.id"
    assert extraction.origin == "contract"


def test_non_id_fields_do_not_generate_variable_extraction():
    endpoint = _build_endpoint(
        response_status_codes=("200",), response_content_types=("application/json",)
    )
    response_schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    assert strategy.variable_extractions == ()


# --- P3.3: nome semântico explícito (json_path != variable_name) ---------------
#
# Fonte: pm.collectionVariables.set(...)/pm.environment.set(...)/etc. no
# script de teste da PRÓPRIA request (api_analysis_engine.
# _extract_defined_variables, exposto via EndpointAnalysis.
# variables_defined) — NUNCA um matching heurístico de nomes ("id" e
# "customer_id" nunca são comparados textualmente entre si).


def test_a_single_defined_variable_names_the_single_id_candidate():
    # Caso A do enunciado: json_path continua "id" (de onde o valor é
    # extraído), mas variable_name vira o nome definido pelo script
    # ("customer_id") — nunca o nome literal do campo.
    endpoint = _build_endpoint(
        response_status_codes=("201",),
        response_content_types=("application/json",),
        variables_defined=("customer_id",),
    )
    response_schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
    }

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    assert len(strategy.variable_extractions) == 1
    extraction = strategy.variable_extractions[0]
    assert extraction.variable_name == "customer_id"
    assert extraction.json_path == "$.id"


def test_no_defined_variable_keeps_the_field_name_as_variable_name():
    # Sem nenhum script definindo variável (variables_defined=(), o
    # default) — comportamento de sempre, nunca alterado: variable_name é
    # o nome literal do campo.
    endpoint = _build_endpoint(
        response_status_codes=("201",),
        response_content_types=("application/json",),
        variables_defined=(),
    )
    response_schema = {"type": "object", "properties": {"id": {"type": "integer"}}}

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    assert strategy.variable_extractions[0].variable_name == "id"


def test_multiple_defined_variables_never_guess_which_one_applies():
    # Ambíguo (dois nomes definidos, um só campo candidato) — nunca
    # adivinha, preserva o nome literal do campo.
    endpoint = _build_endpoint(
        response_status_codes=("201",),
        response_content_types=("application/json",),
        variables_defined=("customer_id", "orderId"),
    )
    response_schema = {"type": "object", "properties": {"id": {"type": "integer"}}}

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    assert strategy.variable_extractions[0].variable_name == "id"


def test_multiple_id_like_fields_never_guess_which_gets_the_defined_name():
    # Ambíguo (um nome definido, dois campos candidatos "*_id"/"id") —
    # nunca adivinha qual dos dois; cada um mantém seu nome literal.
    endpoint = _build_endpoint(
        response_status_codes=("201",),
        response_content_types=("application/json",),
        variables_defined=("customer_id",),
    )
    response_schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "order_id": {"type": "integer"}},
    }

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    variable_names = {extraction.variable_name for extraction in strategy.variable_extractions}
    assert variable_names == {"id", "order_id"}


# --- Required ausente ------------------------------------------------------------


def test_missing_required_field_generates_negative_case():
    endpoint = _build_endpoint()
    request_schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }

    strategy = TestStrategyEngine().build_strategy(endpoint, request_schema=request_schema)

    missing_cases = [
        c for c in strategy.negative_cases if c.case_type == NegativeCaseType.MISSING_REQUIRED_FIELD
    ]
    assert len(missing_cases) == 1
    assert missing_cases[0].field == "name"
    assert "required" in missing_cases[0].evidence


def test_no_negative_cases_without_request_schema():
    endpoint = _build_endpoint()

    strategy = TestStrategyEngine().build_strategy(endpoint)

    assert strategy.negative_cases == ()


# --- Enum inválido -----------------------------------------------------------------


def test_enum_field_generates_negative_case():
    endpoint = _build_endpoint()
    request_schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["active", "inactive"]}},
    }

    strategy = TestStrategyEngine().build_strategy(endpoint, request_schema=request_schema)

    enum_cases = [
        c for c in strategy.negative_cases if c.case_type == NegativeCaseType.INVALID_ENUM_VALUE
    ]
    assert len(enum_cases) == 1
    assert enum_cases[0].field == "status"
    assert "enum" in enum_cases[0].evidence


def test_negative_case_never_copies_sensitive_field_values():
    endpoint = _build_endpoint()
    request_schema = {
        "type": "object",
        "required": ["password"],
        "properties": {"password": {"type": "string", "minLength": 8}},
    }

    strategy = TestStrategyEngine().build_strategy(endpoint, request_schema=request_schema)

    for case in strategy.negative_cases:
        assert "password" == case.field or "password" not in case.description
        assert case.field == "password"


# --- Ambiguidade -----------------------------------------------------------------


def test_no_documented_status_generates_warning_and_no_status_assertion():
    endpoint = _build_endpoint(response_status_codes=())

    strategy = TestStrategyEngine().build_strategy(endpoint)

    assertion_types = {a.assertion_type for a in strategy.assertions}
    assert AssertionType.STATUS_CODE not in assertion_types
    assert any(w.code == "STATUS_CODE_AMBIGUOUS" for w in strategy.warnings)


def test_context_status_overrides_ambiguity():
    endpoint = _build_endpoint(response_status_codes=())

    strategy = TestStrategyEngine().build_strategy(endpoint, expected_status_code=202)

    status_assertion = _assertion_of_type(strategy, AssertionType.STATUS_CODE)
    assert status_assertion.expected_value == 202
    assert status_assertion.origin == "context"
    assert not any(w.code == "STATUS_CODE_AMBIGUOUS" for w in strategy.warnings)


def test_configuration_status_takes_precedence_over_contract():
    endpoint = _build_endpoint(response_status_codes=("200",))
    options = TestStrategyOptions(expected_status_code=206)

    strategy = TestStrategyEngine().build_strategy(endpoint, options=options)

    status_assertion = _assertion_of_type(strategy, AssertionType.STATUS_CODE)
    assert status_assertion.expected_value == 206
    assert status_assertion.origin == "configuration"


# --- Testes contraditórios não coexistem ------------------------------------------


def test_multiple_required_field_assertions_are_not_treated_as_contradictory():
    endpoint = _build_endpoint(
        response_status_codes=("200",), response_content_types=("application/json",)
    )
    response_schema = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
    }

    strategy = TestStrategyEngine().build_strategy(endpoint, response_schema=response_schema)

    required_assertions = [
        a for a in strategy.assertions if a.assertion_type == AssertionType.REQUIRED_FIELD_PRESENT
    ]
    assert len(required_assertions) == 2
    assert not any(w.code == "CONTRADICTORY_ASSERTION" for w in strategy.warnings)


def test_response_time_assertion_only_generated_with_configuration():
    endpoint = _build_endpoint(response_status_codes=("200",))

    without_config = TestStrategyEngine().build_strategy(endpoint)
    with_config = TestStrategyEngine().build_strategy(
        endpoint, options=TestStrategyOptions(max_response_time_ms=500)
    )

    assert AssertionType.RESPONSE_TIME not in {a.assertion_type for a in without_config.assertions}
    response_time_assertion = _assertion_of_type(with_config, AssertionType.RESPONSE_TIME)
    assert response_time_assertion.expected_value == 500
    assert response_time_assertion.origin == "configuration"


# --- Estratégia determinística -----------------------------------------------------


def test_strategy_is_deterministic_across_multiple_runs():
    endpoint = _build_endpoint(
        response_status_codes=("200",), response_content_types=("application/json",)
    )
    response_schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "integer"}, "tag": {"type": "string"}},
    }
    request_schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string", "minLength": 1}},
    }
    engine = TestStrategyEngine()

    first = engine.build_strategy(
        endpoint, response_schema=response_schema, request_schema=request_schema
    )
    second = engine.build_strategy(
        endpoint, response_schema=response_schema, request_schema=request_schema
    )

    assert first == second
