import json

from api_quality_agent.adapters.filesystem import LocalArtifactRepository
from api_quality_agent.application.orchestration import AgentOrchestrator
from api_quality_agent.application.use_cases import GenerateTestsFromDocumentUseCase
from api_quality_agent.application.use_cases.generate_tests_from_document import (
    _MAX_SLUG_LENGTH,
    _slugify,
)
from api_quality_agent.domain.models import ExecutionMode
from api_quality_agent.domain.services import (
    ApiAnalysisEngine,
    DiffEngine,
    ManagedBlockMerger,
    SchemaInferenceEngine,
    TestStrategyEngine,
)
from api_quality_agent.generators import PostmanTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser


def _parse(name: str, items: list) -> object:
    document = {
        "info": {
            "name": name,
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }
    return PostmanCollectionParser().parse_text(json.dumps(document))


def _request(name, item_id, *, method="GET", url="https://x/y", responses=None):
    request: dict = {"name": name, "id": item_id, "request": {"method": method, "url": url}}
    if responses is not None:
        request["response"] = responses
    return request


def _build_use_case(*, artifact_base_path=None, id_factory=lambda: "exec-fixed"):
    orchestrator = AgentOrchestrator(
        ApiAnalysisEngine(),
        SchemaInferenceEngine(),
        TestStrategyEngine(),
        PostmanTestGenerator(),
        ManagedBlockMerger(),
        DiffEngine(),
    )
    artifact_repository = LocalArtifactRepository(artifact_base_path)
    return GenerateTestsFromDocumentUseCase(orchestrator, artifact_repository, id_factory=id_factory)


def test_full_flow_generates_scripts_from_local_document(tmp_path):
    document = _parse(
        "Col Local",
        [
            _request(
                "Criar pet",
                "r1",
                method="POST",
                url="https://x/pets",
                responses=[
                    {"name": "ok", "status": "OK", "code": 201, "header": [], "body": '{"id": 1}'}
                ],
            )
        ],
    )
    use_case = _build_use_case(artifact_base_path=tmp_path)

    result = use_case.execute(document=document)

    assert len(result.endpoint_outcomes) == 1
    outcome = result.endpoint_outcomes[0]
    assert outcome.error is None
    assert "pm.response.to.have.status(201)" in outcome.generated_script.script


def test_execution_context_uses_offline_mode_and_local_file_source(tmp_path):
    document = _parse("Col Local", [_request("Ping", "r1")])
    use_case = _build_use_case(artifact_base_path=tmp_path)

    result = use_case.execute(document=document)

    assert result.execution_context.mode == ExecutionMode.OFFLINE
    assert result.execution_context.source == "local-file"
    assert result.execution_context.workspace_id is None
    assert result.execution_context.collection_id is None
    assert result.execution_context.collection_name == "Col Local"


def test_artifacts_are_saved_under_local_workspace_and_slugified_collection_name(tmp_path):
    document = _parse(
        "Fake Store API Collection",
        [
            _request(
                "Criar pet",
                "r1",
                method="POST",
                url="https://x/pets",
                responses=[{"name": "ok", "status": "OK", "code": 201, "header": [], "body": "{}"}],
            )
        ],
    )
    use_case = _build_use_case(artifact_base_path=tmp_path, id_factory=lambda: "exec-abc")

    result = use_case.execute(document=document)

    assert len(result.artifact_locations) >= 1
    for location in result.artifact_locations:
        assert str(tmp_path / "local" / "Fake_Store_API_Collection" / "exec-abc") in location.path


def test_two_executions_with_different_document_names_are_isolated(tmp_path):
    document_a = _parse("Collection A", [_request("Ping", "r1")])
    document_b = _parse("Collection B", [_request("Ping", "r1")])
    use_case = _build_use_case(artifact_base_path=tmp_path, id_factory=lambda: "exec-fixed")

    result_a = use_case.execute(document=document_a)
    result_b = use_case.execute(document=document_b)

    paths_a = {location.path for location in result_a.artifact_locations}
    paths_b = {location.path for location in result_b.artifact_locations}
    assert paths_a.isdisjoint(paths_b)


def test_diff_reflects_added_managed_block(tmp_path):
    document = _parse("Col Local", [_request("Ping", "r1")])
    use_case = _build_use_case(artifact_base_path=tmp_path)

    result = use_case.execute(document=document)

    assert result.diff.has_changes is True


def test_no_network_dependency_is_required():
    # O use case só depende do orquestrador (puro) e do ArtifactRepository
    # (porta) — nenhum PostmanApiClient, WorkspaceRepository ou
    # CollectionRepository é injetado ou necessário.
    import inspect

    signature = inspect.signature(GenerateTestsFromDocumentUseCase.__init__)
    assert set(signature.parameters) == {"self", "orchestrator", "artifact_repository", "id_factory", "clock"}


# --- _slugify: nomes de arquivo longos (limite MAX_PATH do Windows) ------------------


def test_slugify_within_limit_is_unchanged():
    value = "POST /pets"

    assert _slugify(value) == "POST_pets"


def test_slugify_above_limit_is_truncated_with_hash():
    value = "GET " + "/".join(f"segmento-longo-{i}" for i in range(10))
    sanitized_full = value.replace(" ", "_").replace("/", "_")

    result = _slugify(value)
    prefix, _, hash_suffix = result.rpartition("_")

    assert len(result) <= _MAX_SLUG_LENGTH
    assert sanitized_full.startswith(prefix)
    assert len(hash_suffix) == 8


def test_slugify_truncation_is_deterministic():
    value = "POST " + "x" * 200

    assert _slugify(value) == _slugify(value)


def test_slugify_avoids_collision_for_same_long_prefix():
    prefix = "x" * 200
    value_a = f"{prefix}-a"
    value_b = f"{prefix}-b"

    assert _slugify(value_a) != _slugify(value_b)
