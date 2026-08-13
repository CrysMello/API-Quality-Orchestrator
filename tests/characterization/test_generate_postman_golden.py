"""Caracterização do fluxo `generate --file` (offline, Postman) exatamente
como ele se comporta hoje — antes de qualquer mudança do plano Playwright.

Compara byte a byte com `golden/` (capturado do código real em 2026-08-04,
não escrito à mão). Ver tests/characterization/README.md.
"""

from pathlib import Path

from api_quality_agent.adapters.filesystem import InputResolver, LocalArtifactRepository
from api_quality_agent.application.orchestration import AgentOrchestrator
from api_quality_agent.application.use_cases import GenerateTestsFromDocumentUseCase
from api_quality_agent.domain.services import (
    ApiAnalysisEngine,
    DiffEngine,
    ManagedBlockMerger,
    SchemaInferenceEngine,
    TestStrategyEngine,
)
from api_quality_agent.generators import PostmanTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "acceptance" / "fixtures" / "offline_collection.json"
)
_GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
_FIXED_EXECUTION_ID = "exec-char-001"


def _build_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(
        ApiAnalysisEngine(),
        SchemaInferenceEngine(),
        TestStrategyEngine(),
        PostmanTestGenerator(),
        ManagedBlockMerger(),
        DiffEngine(),
    )


def _run_generate(tmp_path: Path):
    resolved = InputResolver().resolve_from_file(_FIXTURE)
    document = PostmanCollectionParser().parse(resolved)

    artifact_repository = LocalArtifactRepository(tmp_path / "artifacts")
    use_case = GenerateTestsFromDocumentUseCase(
        _build_orchestrator(),
        artifact_repository,
        id_factory=lambda: _FIXED_EXECUTION_ID,
    )
    return use_case.execute(document=document), tmp_path / "artifacts"


def test_artifact_directory_structure_is_unchanged(tmp_path):
    result, artifacts_root = _run_generate(tmp_path)

    relative_paths = sorted(
        Path(location.path).relative_to(artifacts_root).as_posix()
        for location in result.artifact_locations
    )

    # Estrutura atual: local/{collection_slug}/{execution_id}/{category}/{relative_path}
    assert relative_paths == [
        "local/Pets_Offline/exec-char-001/diffs/diff.json",
        "local/Pets_Offline/exec-char-001/scripts/GET_pets.js",
        "local/Pets_Offline/exec-char-001/scripts/POST_pets.js",
    ]


def test_generated_postman_scripts_match_golden(tmp_path):
    result, _artifacts_root = _run_generate(tmp_path)

    scripts_by_source = {
        outcome.endpoint_source: outcome.generated_script.script
        for outcome in result.endpoint_outcomes
        if outcome.generated_script is not None
    }
    assert set(scripts_by_source) == {"POST /pets", "GET /pets"}

    expected_post = (_GOLDEN_DIR / "POST__pets.js").read_text(encoding="utf-8")
    expected_get = (_GOLDEN_DIR / "GET__pets.js").read_text(encoding="utf-8")

    assert scripts_by_source["POST /pets"] == expected_post
    assert scripts_by_source["GET /pets"] == expected_get


def test_diff_json_matches_golden(tmp_path):
    result, artifacts_root = _run_generate(tmp_path)

    diff_location = next(
        location for location in result.artifact_locations if location.path.endswith("diff.json")
    )
    actual_diff = Path(diff_location.path).read_text(encoding="utf-8")
    expected_diff = (_GOLDEN_DIR / "diff.json").read_text(encoding="utf-8")

    assert actual_diff == expected_diff


def test_no_endpoint_processing_errors_for_the_fixture(tmp_path):
    result, _artifacts_root = _run_generate(tmp_path)

    assert len(result.endpoint_outcomes) == 2
    assert all(outcome.error is None for outcome in result.endpoint_outcomes)
