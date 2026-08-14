"""Parte 24 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
padronização de warnings — REUTILIZA PlaywrightGenerationWarning (nenhuma
segunda estrutura equivalente), ampliada com method/location/metadata e
validada contra o catálogo estruturado e estável de warning_catalog.py.
Cobre estrutura, deduplicação, serialização no generation-manifest.json,
máscara de segredo e associação com endpoint/cenário.
"""

import json

import pytest

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    EnvironmentVariable,
    ExecutionContext,
    ExecutionMode,
    PostmanEnvironment,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    ASSERTION_NOT_GENERATED,
    BROAD_STATUS_ASSERTION,
    DefaultPlaywrightTestSuiteBuilder,
    ENDPOINT_NOT_SUPPORTED_YET,
    EXPECTED_STATUS_NOT_DEFINED,
    HTTP_METHOD_NOT_SUPPORTED,
    INFORMATION_INSUFFICIENT,
    PLAYWRIGHT_WARNING_CODES,
    PlaywrightEndpointTestGenerator,
    PlaywrightGenerationWarning,
    UNRESOLVED_VARIABLE,
    URL_NOT_RESOLVED,
)
from api_quality_agent.generators.postman_test_generator import PostmanTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser

_GET_USERS = {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}
_CREATE_USER_REQUEST = {
    "request": {
        "method": "POST",
        "url": "https://api.exemplo.com/users",
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": {"mode": "raw", "raw": '{"name": "Ana"}'},
    }
}


def _status_assertion(status_code: int = 200, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
        origin=origin,
    )


def _valid_json_body_assertion() -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.VALID_JSON_BODY,
        description="O corpo da resposta deve ser um JSON válido.",
        expected_value=None,
        origin="contract",
    )


def _schema_assertion(schema: dict) -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.SCHEMA,
        description="O corpo da resposta deve validar contra o schema esperado.",
        expected_value=schema,
        origin="contract",
    )


def _analyzed(request: dict):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Collection",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": [{"name": "R1", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _generate(request: dict, assertions: tuple[AssertionDefinition, ...] = ()):
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)


# --- estrutura: code/message/endpoint/method/scenario/location/metadata ----


def test_warning_carries_all_standardized_fields():
    warning = PlaywrightGenerationWarning(
        code="SENSITIVE_HEADER_OMITTED",
        message="Header 'X-Api-Key' omitido: valor não é uma referência de variável resolvível.",
        endpoint="GET /users",
        scenario="success",
        location="header",
        metadata=(("header", "X-Api-Key"),),
    )

    assert warning.code == "SENSITIVE_HEADER_OMITTED"
    assert warning.endpoint == "GET /users"
    assert warning.scenario == "success"
    assert warning.location == "header"
    assert warning.metadata == (("header", "X-Api-Key"),)
    # method: derivado automaticamente de "endpoint" (regra "padronizar
    # warnings com... method"), nunca precisa ser informado à parte.
    assert warning.method == "GET"


def test_method_is_never_recomputed_when_explicitly_provided():
    warning = PlaywrightGenerationWarning(
        code="SENSITIVE_HEADER_OMITTED",
        message="...",
        endpoint="GET /users",
        scenario=None,
        method="OPTIONS",
    )

    assert warning.method == "OPTIONS"


def test_warning_code_cannot_be_free_text():
    # Regra 1 da Parte 24: "warning não pode ser apenas texto livre" — só um
    # código já registrado em PLAYWRIGHT_WARNING_CODES é aceito.
    with pytest.raises(ValueError, match="warning_catalog.PLAYWRIGHT_WARNING_CODES"):
        PlaywrightGenerationWarning(
            code="ALGO_INVENTADO_NA_HORA",
            message="...",
            endpoint="GET /users",
            scenario=None,
        )


def test_catalog_contains_all_the_mandatory_codes():
    mandatory = {
        ASSERTION_NOT_GENERATED,
        INFORMATION_INSUFFICIENT,
        BROAD_STATUS_ASSERTION,
        UNRESOLVED_VARIABLE,
        HTTP_METHOD_NOT_SUPPORTED,
        URL_NOT_RESOLVED,
    }
    assert mandatory <= PLAYWRIGHT_WARNING_CODES


def test_catalog_still_contains_every_code_used_before_this_part():
    # "e os demais já existentes no projeto" — nenhum código anterior foi
    # removido do catálogo, mesmo quando nenhum caminho de geração o emite
    # mais (ENDPOINT_NOT_SUPPORTED_YET — ver test_generator_no_longer_
    # emits_the_generic_endpoint_not_supported_code abaixo).
    pre_existing = {
        "HEADER_VALUE_NOT_RESOLVED",
        "SENSITIVE_HEADER_OMITTED",
        "RESERVED_HEADER_OMITTED",
        "DUPLICATE_HEADER_IGNORED",
        "AUTHENTICATION_NOT_SUPPORTED",
        "AUTHENTICATION_VALUE_NOT_RESOLVED",
        "BODY_NOT_SUPPORTED",
        "BODY_JSON_INVALID",
        "MULTIPART_FILE_NOT_RESOLVED",
        "EXPECTED_STATUS_NOT_DEFINED",
        "BODY_STRUCTURE_NOT_DETERMINED",
        "JSON_SCHEMA_REF_NOT_SUPPORTED",
        "FILE_NAME_COLLISION_RESOLVED",
        ENDPOINT_NOT_SUPPORTED_YET,
    }
    assert pre_existing <= PLAYWRIGHT_WARNING_CODES


# --- associação com endpoint e cenário: fim a fim pelo gerador real --------


def test_http_method_not_supported_is_associated_with_endpoint_and_method():
    generated = _generate({"request": {"method": "DELETE", "url": "https://api.exemplo.com/x"}})

    warning = next(w for w in generated.warnings if w.code == HTTP_METHOD_NOT_SUPPORTED)
    assert warning.endpoint == "DELETE /x"
    assert warning.method == "DELETE"
    assert warning.location == "method"
    # Fallback: nunca um cenário de sucesso de verdade — scenario=None
    # (mesmo critério de "não gerado", distinto de um warning PARCIAL de um
    # cenário que existe).
    assert warning.scenario is None


def test_url_not_resolved_replaces_the_generic_code_for_unresolved_path_variables():
    generated = _generate(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/users/:id"}}
    )

    warning = next(w for w in generated.warnings if w.code == URL_NOT_RESOLVED)
    assert warning.location == "url"
    assert warning.endpoint == "GET /users/:id"


def test_generator_no_longer_emits_the_generic_endpoint_not_supported_code():
    # ENDPOINT_NOT_SUPPORTED_YET continua no catálogo (estabilidade), mas
    # HTTP_METHOD_NOT_SUPPORTED/URL_NOT_RESOLVED (mais específicos) o
    # substituem nos dois casos que ele cobria.
    scenarios = (
        {"request": {"method": "DELETE", "url": "https://api.exemplo.com/x"}},
        {"request": {"method": "GET", "url": "https://api.exemplo.com/users/:id"}},
    )
    for request in scenarios:
        generated = _generate(request)
        assert all(w.code != ENDPOINT_NOT_SUPPORTED_YET for w in generated.warnings)


def test_assertion_not_generated_is_associated_with_the_success_scenario():
    schema = {"type": "object", "properties": {"value": {"type": ["string", "integer"]}}}
    generated = _generate(
        _GET_USERS, (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema))
    )

    warning = next(w for w in generated.warnings if w.code == ASSERTION_NOT_GENERATED)
    assert warning.endpoint == "GET /users"
    assert warning.scenario == "success"
    assert warning.location == "body"
    assert warning.metadata == (("field", "value"),)
    assert "'value'" in warning.message


def test_information_insufficient_is_associated_with_the_success_scenario():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "x-source-request-field": "nickname"}},
    }
    generated = _generate(
        _CREATE_USER_REQUEST,
        (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema)),
    )

    warning = next(w for w in generated.warnings if w.code == INFORMATION_INSUFFICIENT)
    assert warning.endpoint == "POST /users"
    assert warning.scenario == "success"
    assert warning.location == "body"
    assert warning.metadata == (("field", "name"), ("source_field", "nickname"))


def test_broad_status_assertion_is_associated_with_the_success_scenario():
    generated = _generate(_GET_USERS)

    warning = next(w for w in generated.warnings if w.code == BROAD_STATUS_ASSERTION)
    assert warning.endpoint == "GET /users"
    assert warning.scenario == "success"
    assert warning.location == "status"


# --- regra 3: warning nunca transforma o cenário em erro --------------------


def test_a_rendered_scenario_with_warnings_is_still_a_real_test_not_a_skip():
    generated = _generate(_GET_USERS)  # sem status assertion -> BROAD + warnings

    assert generated.scenario_names == ("success",)
    assert "@pytest.mark.skip" not in generated.content
    assert generated.warnings != ()


# --- máscara de segredo: regra 6 -------------------------------------------


def test_sensitive_header_warning_never_leaks_the_secret_value():
    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(
                key="apiKey", value="valor-secreto-do-environment", is_secret=True, enabled=True
            ),
        ),
    )
    analysis, normalized_request = _analyzed(
        {
            "request": {
                "method": "GET",
                "url": "https://api.exemplo.com/users",
                "header": [{"key": "X-Api-Key", "value": "valor-secreto-do-environment"}],
            }
        }
    )
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=(_status_assertion(),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    generated = PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy, normalized_request, environment
    )

    warning = next(w for w in generated.warnings if w.code == "SENSITIVE_HEADER_OMITTED")
    assert "valor-secreto-do-environment" not in warning.message
    assert all("valor-secreto-do-environment" not in value for _, value in warning.metadata)
    assert "valor-secreto-do-environment" not in generated.content


def test_no_generated_warning_ever_contains_a_bearer_or_authorization_value():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": "https://api.exemplo.com/users",
                "header": [{"key": "Authorization", "value": "Bearer super-secreto-token"}],
            }
        },
        (_status_assertion(),),
    )

    for warning in generated.warnings:
        assert "super-secreto-token" not in warning.message
        assert all("super-secreto-token" not in value for _, value in warning.metadata)


# --- deduplicação (regra 2) e serialização no manifesto ---------------------


def _context() -> ExecutionContext:
    return ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="Col")


def _manifest_payload(endpoint_tests):
    suite = DefaultPlaywrightTestSuiteBuilder().build(endpoint_tests, _context())
    manifest_file = next(f for f in suite.files if f.relative_path == "generation-manifest.json")
    return json.loads(manifest_file.content)


def test_identical_warnings_across_endpoints_are_deduplicated_in_the_manifest():
    from api_quality_agent.generators.playwright import GeneratedEndpointTest

    warning = PlaywrightGenerationWarning(
        code="SENSITIVE_HEADER_OMITTED",
        message="Header 'Authorization' omitido: cabeçalhos de autenticação ainda não são "
        "gerados automaticamente.",
        endpoint="GET /pets",
        scenario=None,
        location="header",
    )
    # Mesmo warning de conteúdo, "duplicado" por vir de duas fontes
    # diferentes (naming + endpoint) — cenário artificial só para provar a
    # deduplicação; o mecanismo funciona por CONTEÚDO, não por identidade.
    endpoint_tests = [
        GeneratedEndpointTest(
            endpoint_source="GET /pets",
            suggested_file_name="ignored.py",
            content="def test_x(): ...\n",
            scenario_names=("success",),
            warnings=(warning, warning),
        )
    ]

    payload = _manifest_payload(endpoint_tests)

    matching = [w for w in payload["warnings"] if w["code"] == "SENSITIVE_HEADER_OMITTED"]
    assert len(matching) == 1


def test_distinct_warnings_with_the_same_code_are_never_collapsed():
    from api_quality_agent.generators.playwright import GeneratedEndpointTest

    first = PlaywrightGenerationWarning(
        code="DUPLICATE_HEADER_IGNORED",
        message="Header 'X-Custom' repetido.",
        endpoint="GET /pets",
        scenario=None,
    )
    second = PlaywrightGenerationWarning(
        code="DUPLICATE_HEADER_IGNORED",
        message="Header 'Accept' repetido.",
        endpoint="GET /pets",
        scenario=None,
    )
    endpoint_tests = [
        GeneratedEndpointTest(
            endpoint_source="GET /pets",
            suggested_file_name="ignored.py",
            content="def test_x(): ...\n",
            scenario_names=("success",),
            warnings=(first, second),
        )
    ]

    payload = _manifest_payload(endpoint_tests)

    matching = [w for w in payload["warnings"] if w["code"] == "DUPLICATE_HEADER_IGNORED"]
    assert len(matching) == 2


def test_manifest_warning_entries_round_trip_through_json():
    generated = _generate(_GET_USERS)  # BROAD + EXPECTED_STATUS_NOT_DEFINED

    payload = _manifest_payload([generated])

    codes = {w["code"] for w in payload["warnings"]}
    assert codes == {EXPECTED_STATUS_NOT_DEFINED, BROAD_STATUS_ASSERTION}
    for entry in payload["warnings"]:
        assert set(entry.keys()) == {
            "code",
            "endpoint",
            "method",
            "scenario",
            "location",
            "message",
            "metadata",
        }
        assert entry["endpoint"] == "GET /users"
        assert entry["method"] == "GET"
        assert entry["scenario"] == "success"
        assert entry["location"] == "status"
        assert isinstance(entry["metadata"], dict)
    # JSON válido de ponta a ponta (regra "persistir no generation-
    # manifest.json") — já garantido por _manifest_payload usar json.loads,
    # reforçado aqui explicitamente.
    reencoded = json.dumps(payload, ensure_ascii=False)
    assert json.loads(reencoded) == payload


# --- regra 4: cenário completo / parcial / não gerado -----------------------


def test_manifest_distinguishes_complete_partial_and_not_generated_coverage():
    complete = _generate(_GET_USERS, (_status_assertion(),))
    partial = _generate(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/orders"}}
    )  # sem status assertion -> BROAD
    not_generated = _generate(
        {"request": {"method": "DELETE", "url": "https://api.exemplo.com/x"}}
    )

    payload = _manifest_payload([complete, partial, not_generated])

    coverage_by_endpoint = {entry["endpoint"]: entry["coverage"] for entry in payload["endpoints"]}
    assert coverage_by_endpoint["GET /users"] == "complete"
    assert coverage_by_endpoint["GET /orders"] == "partial"
    assert coverage_by_endpoint["DELETE /x"] == "not_generated"


# --- fluxo Postman preservado -------------------------------------------------


def test_postman_flow_is_never_touched_by_the_warning_catalog():
    strategy = TestStrategy(
        endpoint_source="GET /users",
        assertions=(_status_assertion(201),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PostmanTestGenerator().generate(strategy)

    assert "pm.response.to.have.status(201);" in generated.script
