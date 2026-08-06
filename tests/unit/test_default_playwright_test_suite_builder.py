"""Parte 06 do plano de ação Playwright: DefaultPlaywrightTestSuiteBuilder —
implementação concreta do contrato PlaywrightTestSuiteBuilder (Parte 03).
"""

import ast
import json

from api_quality_agent.domain.models import ExecutionContext, ExecutionMode
from api_quality_agent.generators.playwright import (
    DefaultPlaywrightTestSuiteBuilder,
    GeneratedEndpointTest,
    PlaywrightGenerationWarning,
)


def _context() -> ExecutionContext:
    return ExecutionContext.create(
        mode=ExecutionMode.OFFLINE, source="test", collection_name="Pets"
    )


def _endpoint_test(endpoint_source: str, **overrides) -> GeneratedEndpointTest:
    defaults = {
        "endpoint_source": endpoint_source,
        "suggested_file_name": "ignored.py",  # o builder resolve o nome de novo
        "content": "def test_x(): ...\n",
        "scenario_names": (),
        "warnings": (),
    }
    defaults.update(overrides)
    return GeneratedEndpointTest(**defaults)


def test_build_produces_conftest_endpoints_and_manifest():
    endpoint_tests = [_endpoint_test("GET /pets"), _endpoint_test("POST /pets")]

    suite = DefaultPlaywrightTestSuiteBuilder().build(endpoint_tests, _context())

    relative_paths = {generated_file.relative_path for generated_file in suite.files}
    assert relative_paths == {
        "conftest.py",
        "endpoints/test_get_pets.py",
        "endpoints/test_post_pets.py",
        "generation-manifest.json",
    }


def test_endpoint_file_content_matches_the_corresponding_generated_endpoint_test():
    endpoint_tests = [_endpoint_test("GET /pets", content="# conteúdo do GET\n")]

    suite = DefaultPlaywrightTestSuiteBuilder().build(endpoint_tests, _context())

    endpoint_file = next(f for f in suite.files if f.relative_path == "endpoints/test_get_pets.py")
    assert endpoint_file.content == "# conteúdo do GET\n"


def test_conftest_content_is_syntactically_valid_python():
    suite = DefaultPlaywrightTestSuiteBuilder().build([], _context())

    conftest_file = next(f for f in suite.files if f.relative_path == "conftest.py")
    ast.parse(conftest_file.content)


def test_manifest_is_valid_json_with_minimal_expected_fields():
    endpoint_tests = [_endpoint_test("GET /pets"), _endpoint_test("POST /pets")]
    context = _context()

    suite = DefaultPlaywrightTestSuiteBuilder().build(endpoint_tests, context)

    manifest_file = next(f for f in suite.files if f.relative_path == "generation-manifest.json")
    payload = json.loads(manifest_file.content)

    assert payload["target"] == "playwright"
    assert payload["execution_id"] == context.execution_id
    assert payload["endpoints_analyzed"] == 2
    assert payload["endpoint_files_generated"] == 2


def test_collisions_are_resolved_and_produce_warnings():
    # Dois endpoints diferentes cujo nome sanitizado colide (Parte 05).
    endpoint_tests = [_endpoint_test("GET /users/{id}"), _endpoint_test("GET /users/:id")]

    suite = DefaultPlaywrightTestSuiteBuilder().build(endpoint_tests, _context())

    relative_paths = {generated_file.relative_path for generated_file in suite.files}
    assert "endpoints/test_get_users_by_id.py" in relative_paths
    assert "endpoints/test_get_users_by_id_02.py" in relative_paths
    assert any(warning.code == "FILE_NAME_COLLISION_RESOLVED" for warning in suite.warnings)


def test_endpoint_level_warnings_are_aggregated_into_the_suite():
    endpoint_warning = PlaywrightGenerationWarning(
        code="ASSERTION_NOT_GENERATED",
        message="Sem evidência suficiente.",
        endpoint="GET /pets",
        scenario="success",
    )
    endpoint_tests = [_endpoint_test("GET /pets", warnings=(endpoint_warning,))]

    suite = DefaultPlaywrightTestSuiteBuilder().build(endpoint_tests, _context())

    assert endpoint_warning in suite.warnings


def test_build_with_no_endpoints_still_produces_conftest_and_manifest():
    suite = DefaultPlaywrightTestSuiteBuilder().build([], _context())

    relative_paths = {generated_file.relative_path for generated_file in suite.files}
    assert relative_paths == {"conftest.py", "generation-manifest.json"}
