"""Parte 15 do plano de ação Playwright: trava o formato do
generation-manifest.json ampliado (rastreabilidade de variáveis) — mesmo
espírito de tests/characterization/test_execution_result_schema.py (schema
travado por asserção direta, sem depender de uma lib externa de JSON
Schema, que este projeto não tem como dependência).

Se este teste quebrar por uma mudança INTENCIONAL de schema, bump
_MANIFEST_SCHEMA_VERSION em default_playwright_test_suite_builder.py e
atualize as asserções aqui conscientemente — não é uma regressão, é o
critério de aceite "manifesto validado por schema ou teste equivalente"
fazendo seu trabalho.
"""

import json

from api_quality_agent.domain.models import ExecutionContext, ExecutionMode
from api_quality_agent.generators.playwright import (
    DefaultPlaywrightTestSuiteBuilder,
    GeneratedEndpointTest,
    PlaywrightGenerationWarning,
    UnresolvedVariable,
)

_TOP_LEVEL_KEYS = {
    "schema_version",
    "target",
    "collection_name",
    "execution_id",
    "generated_at",
    "endpoints_analyzed",
    "endpoint_files_generated",
    "endpoints",
    "endpoints_not_rendered",
    "required_environment_variables",
    "resolved_variables",
    "warnings",
}

_ENDPOINT_ENTRY_KEYS = {"endpoint", "method", "path", "file", "rendered"}

# Duas formas de warning coexistem no mesmo array: um PlaywrightGenerationWarning
# "de código" (code/endpoint/scenario/message) e o UNRESOLVED_VARIABLE "de
# variável" (code/endpoint/variable/location) — nunca as duas junto na mesma
# entrada.
_CODE_WARNING_KEYS = {"code", "endpoint", "scenario", "message"}
_VARIABLE_WARNING_KEYS = {"code", "endpoint", "variable", "location"}


def _context() -> ExecutionContext:
    return ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="Pets")


def _endpoint_test(endpoint_source: str, **overrides) -> GeneratedEndpointTest:
    defaults = {
        "endpoint_source": endpoint_source,
        "suggested_file_name": "ignored.py",
        "content": "def test_x(api_context): ...\n",
        "scenario_names": ("success",),
        "warnings": (),
    }
    defaults.update(overrides)
    return GeneratedEndpointTest(**defaults)


def _manifest_payload(endpoint_tests) -> dict:
    suite = DefaultPlaywrightTestSuiteBuilder().build(endpoint_tests, _context())
    manifest_file = next(f for f in suite.files if f.relative_path == "generation-manifest.json")
    return json.loads(manifest_file.content)


def test_schema_version_is_1_0():
    payload = _manifest_payload([_endpoint_test("GET /pets")])

    assert payload["schema_version"] == "1.0"


def test_top_level_keys_match_exactly():
    payload = _manifest_payload([_endpoint_test("GET /pets")])

    assert set(payload.keys()) == _TOP_LEVEL_KEYS


def test_endpoint_entry_keys_and_values():
    endpoint_tests = [
        _endpoint_test(
            "GET /pets",
            required_environment_variables=("AQO_API_KEY",),
            resolved_variables=(("baseUrl", "api.exemplo.com"),),
        )
    ]

    payload = _manifest_payload(endpoint_tests)

    assert len(payload["endpoints"]) == 1
    entry = payload["endpoints"][0]
    assert set(entry.keys()) == _ENDPOINT_ENTRY_KEYS
    assert entry["endpoint"] == "GET /pets"
    assert entry["method"] == "GET"
    assert entry["path"] == "/pets"
    assert entry["file"] == "endpoints/test_get_pets.py"
    assert entry["rendered"] is True


def test_endpoint_file_matches_the_actually_resolved_name_after_a_collision():
    # "O manifesto lista todos os arquivos realmente existentes": o nome
    # aqui precisa ser o já resolvido por resolve_endpoint_file_names (com
    # sufixo), nunca o nome "cru" que colidiria com outro endpoint.
    endpoint_tests = [_endpoint_test("GET /users/{id}"), _endpoint_test("GET /users/:id")]

    payload = _manifest_payload(endpoint_tests)

    files = {entry["file"] for entry in payload["endpoints"]}
    assert files == {
        "endpoints/test_get_users_by_id.py",
        "endpoints/test_get_users_by_id_02.py",
    }


def test_fallback_endpoint_is_marked_not_rendered():
    endpoint_tests = [
        _endpoint_test("GET /pets", scenario_names=("success",)),
        _endpoint_test("POST /pets", scenario_names=()),
    ]

    payload = _manifest_payload(endpoint_tests)

    rendered_by_endpoint = {entry["endpoint"]: entry["rendered"] for entry in payload["endpoints"]}
    assert rendered_by_endpoint == {"GET /pets": True, "POST /pets": False}
    assert payload["endpoints_not_rendered"] == ["POST /pets"]


def test_required_environment_variables_are_merged_deduplicated_and_sorted():
    endpoint_tests = [
        _endpoint_test("GET /pets", required_environment_variables=("AQO_BETA", "AQO_ALPHA")),
        _endpoint_test("POST /pets", required_environment_variables=("AQO_ALPHA",)),
    ]

    payload = _manifest_payload(endpoint_tests)

    assert payload["required_environment_variables"] == ["AQO_ALPHA", "AQO_BETA"]


def test_resolved_variables_are_merged_across_endpoints():
    endpoint_tests = [
        _endpoint_test("GET /pets", resolved_variables=(("baseUrl", "api.exemplo.com"),)),
        _endpoint_test("POST /pets", resolved_variables=(("region", "us-east-1"),)),
    ]

    payload = _manifest_payload(endpoint_tests)

    assert payload["resolved_variables"] == {
        "baseUrl": "api.exemplo.com",
        "region": "us-east-1",
    }


def test_resolved_variables_never_contain_a_secret_by_construction():
    # O manifesto só reflete o que o gerador já filtrou (Parte 15 nunca
    # grava um secret em GeneratedEndpointTest.resolved_variables) — este
    # teste documenta a garantia no ponto de consumo, não reimplementa o
    # filtro.
    endpoint_tests = [_endpoint_test("GET /pets", resolved_variables=(("apiKey", "not-a-secret"),))]

    payload = _manifest_payload(endpoint_tests)

    assert payload["resolved_variables"] == {"apiKey": "not-a-secret"}


def test_code_warning_entry_shape():
    warning = PlaywrightGenerationWarning(
        code="SENSITIVE_HEADER_OMITTED",
        message="Header 'Authorization' omitido: ...",
        endpoint="GET /pets",
        scenario=None,
    )
    endpoint_tests = [_endpoint_test("GET /pets", warnings=(warning,))]

    payload = _manifest_payload(endpoint_tests)

    assert len(payload["warnings"]) == 1
    entry = payload["warnings"][0]
    assert set(entry.keys()) == _CODE_WARNING_KEYS
    assert entry["code"] == "SENSITIVE_HEADER_OMITTED"
    assert entry["endpoint"] == "GET /pets"


def test_unresolved_variable_warning_entry_shape_matches_the_plan_example():
    # Exemplo do plano de ação: {"code": "UNRESOLVED_VARIABLE", "endpoint":
    # "GET /users/{id}", "variable": "userId", "location": "path"}.
    endpoint_tests = [
        _endpoint_test(
            "GET /users/{id}",
            scenario_names=(),
            unresolved_variables=(UnresolvedVariable(name="userId", location="path"),),
        )
    ]

    payload = _manifest_payload(endpoint_tests)

    assert len(payload["warnings"]) == 1
    entry = payload["warnings"][0]
    assert set(entry.keys()) == _VARIABLE_WARNING_KEYS
    assert entry == {
        "code": "UNRESOLVED_VARIABLE",
        "endpoint": "GET /users/{id}",
        "variable": "userId",
        "location": "path",
    }


def test_manifest_still_reports_endpoints_analyzed_and_files_generated():
    # Compatibilidade com o manifesto mínimo da Parte 06 — nunca removido,
    # só ampliado.
    endpoint_tests = [_endpoint_test("GET /pets"), _endpoint_test("POST /pets")]

    payload = _manifest_payload(endpoint_tests)

    assert payload["endpoints_analyzed"] == 2
    assert payload["endpoint_files_generated"] == 2


def test_empty_suite_produces_empty_but_well_shaped_manifest():
    payload = _manifest_payload([])

    assert payload["endpoints"] == []
    assert payload["endpoints_not_rendered"] == []
    assert payload["required_environment_variables"] == []
    assert payload["resolved_variables"] == {}
    assert payload["warnings"] == []
