"""Parte 06 do plano de ação Playwright: GeneratePlaywrightTestSuiteUseCase —
pipeline independente do AgentOrchestrator, com persistência isolada por
execution_id (mesma estrutura física: scripts/playwright/...).
"""

import json
from pathlib import Path

from api_quality_agent.adapters.filesystem import LocalArtifactRepository
from api_quality_agent.application.use_cases import GeneratePlaywrightTestSuiteUseCase
from api_quality_agent.domain.services import (
    ApiAnalysisEngine,
    InferenceSchemaProvider,
    SchemaInferenceEngine,
    TestStrategyEngine,
)
from api_quality_agent.generators.playwright import (
    DefaultPlaywrightTestSuiteBuilder,
    PlaceholderEndpointTestGenerator,
)
from api_quality_agent.parsers import PostmanCollectionParser


def _document(items: list):
    return PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Pets",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": items,
            }
        )
    )


def _build_use_case(tmp_path: Path, *, id_factory=None) -> GeneratePlaywrightTestSuiteUseCase:
    kwargs = {"id_factory": id_factory} if id_factory is not None else {}
    return GeneratePlaywrightTestSuiteUseCase(
        ApiAnalysisEngine(),
        InferenceSchemaProvider(SchemaInferenceEngine()),
        TestStrategyEngine(),
        PlaceholderEndpointTestGenerator(),
        DefaultPlaywrightTestSuiteBuilder(),
        LocalArtifactRepository(tmp_path / "artifacts"),
        **kwargs,
    )


def _two_endpoints_document():
    return _document(
        [
            {"name": "R1", "id": "r1", "request": {"method": "GET", "url": "https://x/pets"}},
            {"name": "R2", "id": "r2", "request": {"method": "POST", "url": "https://x/pets"}},
        ]
    )


def test_execute_returns_a_result_with_one_endpoint_file_per_request(tmp_path):
    use_case = _build_use_case(tmp_path)

    result = use_case.execute(document=_two_endpoints_document())

    endpoint_paths = [
        path for path in result.generated_file_paths if path.startswith("endpoints/")
    ]
    assert len(endpoint_paths) == 2


def test_execute_result_includes_conftest_and_manifest_paths(tmp_path):
    use_case = _build_use_case(tmp_path)

    result = use_case.execute(document=_two_endpoints_document())

    assert "conftest.py" in result.generated_file_paths
    assert "generation-manifest.json" in result.generated_file_paths
    assert result.warning_count == 0


def test_execute_persists_files_under_scripts_playwright_category(tmp_path):
    use_case = _build_use_case(tmp_path, id_factory=lambda: "exec-1")

    use_case.execute(document=_two_endpoints_document())

    base = tmp_path / "artifacts" / "local" / "Pets" / "exec-1" / "scripts" / "playwright"
    assert (base / "conftest.py").exists()
    assert (base / "generation-manifest.json").exists()
    assert (base / "endpoints").is_dir()
    assert len(list((base / "endpoints").glob("*.py"))) == 2


def test_execute_uses_local_workspace_and_slugified_collection_when_offline(tmp_path):
    use_case = _build_use_case(tmp_path, id_factory=lambda: "exec-2")

    use_case.execute(document=_document([]))

    assert (tmp_path / "artifacts" / "local" / "Pets" / "exec-2").exists()


def test_execute_uses_real_workspace_and_collection_ids_when_provided(tmp_path):
    use_case = _build_use_case(tmp_path, id_factory=lambda: "exec-3")

    use_case.execute(
        document=_document([]),
        workspace_id="ws-1",
        workspace_name="QA Workspace",
        collection_id="col-1",
        collection_name="Pets API",
    )

    assert (tmp_path / "artifacts" / "ws-1" / "col-1" / "exec-3").exists()
    assert not (tmp_path / "artifacts" / "local").exists()


def test_two_executions_do_not_overwrite_each_other(tmp_path):
    ids = iter(["exec-a", "exec-b"])
    use_case = _build_use_case(tmp_path, id_factory=lambda: next(ids))

    use_case.execute(document=_two_endpoints_document())
    use_case.execute(document=_two_endpoints_document())

    manifests = list((tmp_path / "artifacts").rglob("generation-manifest.json"))
    assert len(manifests) == 2

    execution_dirs = {manifest.parents[2].name for manifest in manifests}
    assert execution_dirs == {"exec-a", "exec-b"}


def test_generated_manifest_reflects_the_correct_execution_id(tmp_path):
    use_case = _build_use_case(tmp_path, id_factory=lambda: "exec-manifest")

    use_case.execute(document=_two_endpoints_document())

    manifest_path = (
        tmp_path
        / "artifacts"
        / "local"
        / "Pets"
        / "exec-manifest"
        / "scripts"
        / "playwright"
        / "generation-manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["execution_id"] == "exec-manifest"
    assert payload["endpoints_analyzed"] == 2
