import json

import pytest

from api_quality_agent.adapters.filesystem import LocalArtifactRepository
from api_quality_agent.application.orchestration import AgentOrchestrator
from api_quality_agent.application.use_cases import (
    GenerateCollectionTestsUseCase,
    GetCurrentWorkspaceUseCase,
    ResolveCollectionUseCase,
)
from api_quality_agent.application.use_cases.generate_collection_tests import (
    _MAX_FILENAME_LENGTH,
    _sanitize_filename,
)
from api_quality_agent.domain.exceptions import InputError, ResourceNotFoundError
from api_quality_agent.domain.models import ActiveSelection, CollectionRef
from api_quality_agent.domain.services import (
    ApiAnalysisEngine,
    CollectionSelectionService,
    DiffEngine,
    ManagedBlockMerger,
    SchemaInferenceEngine,
    TestStrategyEngine,
)
from api_quality_agent.generators import PostmanTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser


class _InMemorySelectionRepository:
    def __init__(self, initial: ActiveSelection | None = None) -> None:
        self._selection = initial or ActiveSelection()

    def load(self) -> ActiveSelection:
        return self._selection

    def save(self, selection: ActiveSelection) -> None:
        self._selection = selection


class _FakeCollectionRepository:
    def __init__(self, refs: tuple[CollectionRef, ...], documents_by_id: dict) -> None:
        self._refs = refs
        self._documents_by_id = documents_by_id
        self.get_calls: list[str] = []

    def list(self, workspace_id: str) -> tuple[CollectionRef, ...]:
        return self._refs

    def get(self, collection_id: str):
        self.get_calls.append(collection_id)
        return self._documents_by_id[collection_id]

    # Deliberadamente NÃO existe um método update(): a ausência garante,
    # estruturalmente, que nenhum caminho desta etapa pode chamá-lo.


def _parse(name: str, items: list) -> object:
    document = {
        "info": {
            "name": name,
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }
    return PostmanCollectionParser().parse_text(json.dumps(document))


def _request(name, item_id, *, method="GET", url="https://x/y", events=None, responses=None):
    request: dict = {"name": name, "id": item_id, "request": {"method": method, "url": url}}
    if events is not None:
        request["event"] = events
    if responses is not None:
        request["response"] = responses
    return request


def _build_use_case(
    *,
    collection_repository,
    selection_repository,
    id_factory=lambda: "exec-fixed",
    artifact_base_path=None,
):
    selection_service = CollectionSelectionService(collection_repository)
    orchestrator = AgentOrchestrator(
        ApiAnalysisEngine(),
        SchemaInferenceEngine(),
        TestStrategyEngine(),
        PostmanTestGenerator(),
        ManagedBlockMerger(),
        DiffEngine(),
    )
    artifact_repository = LocalArtifactRepository(artifact_base_path)
    use_case = GenerateCollectionTestsUseCase(
        GetCurrentWorkspaceUseCase(selection_repository),
        ResolveCollectionUseCase(selection_service, collection_repository, selection_repository),
        collection_repository,
        orchestrator,
        artifact_repository,
        id_factory=id_factory,
    )
    return use_case


# --- Fluxo completo com fakes / Collection ativa ------------------------------------


def test_full_flow_with_active_collection(tmp_path):
    document = _parse(
        "Col",
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
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    selection_repository = _InMemorySelectionRepository(
        ActiveSelection(workspace_id="ws-1", collection_id="c1")
    )
    use_case = _build_use_case(
        collection_repository=collection_repository,
        selection_repository=selection_repository,
        artifact_base_path=tmp_path,
    )

    result = use_case.execute()

    assert result.execution_context.workspace_id == "ws-1"
    assert result.execution_context.collection_id == "c1"
    assert len(result.endpoint_outcomes) == 1
    outcome = result.endpoint_outcomes[0]
    assert outcome.error is None
    assert outcome.generated_script is not None
    assert "pm.response.to.have.status(201)" in outcome.generated_script.script
    assert collection_repository.get_calls == ["c1"]


# --- Collection temporária -----------------------------------------------------------


def test_temporary_collection_override_does_not_persist_selection():
    document_a = _parse("A", [_request("R", "r1")])
    document_b = _parse("B", [_request("R", "r1")])
    collection_repository = _FakeCollectionRepository(
        (
            CollectionRef(id="ca", name="Collection A", workspace_id="ws-1"),
            CollectionRef(id="cb", name="Collection B", workspace_id="ws-1"),
        ),
        {"ca": document_a, "cb": document_b},
    )
    selection_repository = _InMemorySelectionRepository(
        ActiveSelection(workspace_id="ws-1", collection_id="ca")
    )
    use_case = _build_use_case(
        collection_repository=collection_repository, selection_repository=selection_repository
    )

    result = use_case.execute(collection_id="cb")

    assert result.execution_context.collection_id == "cb"
    # a seleção persistida (ativa) permanece intocada
    assert selection_repository.load().collection_id == "ca"


# --- Sem seleção ---------------------------------------------------------------------


def test_raises_when_no_active_workspace():
    document = _parse("Col", [_request("R", "r1")])
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    selection_repository = _InMemorySelectionRepository()  # sem workspace
    use_case = _build_use_case(
        collection_repository=collection_repository, selection_repository=selection_repository
    )

    with pytest.raises(InputError):
        use_case.execute()


def test_raises_when_no_collection_available():
    document = _parse("Col", [_request("R", "r1")])
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    selection_repository = _InMemorySelectionRepository(ActiveSelection(workspace_id="ws-1"))
    use_case = _build_use_case(
        collection_repository=collection_repository, selection_repository=selection_repository
    )

    with pytest.raises(ResourceNotFoundError):
        use_case.execute()


# --- Dois requests ---------------------------------------------------------------------


def test_two_requests_produce_two_outcomes():
    document = _parse(
        "Col",
        [
            _request(
                "Criar pet",
                "r1",
                method="POST",
                url="https://x/pets",
                responses=[{"name": "ok", "status": "OK", "code": 201, "header": [], "body": "{}"}],
            ),
            _request(
                "Listar pets",
                "r2",
                method="GET",
                url="https://x/pets",
                responses=[{"name": "ok", "status": "OK", "code": 200, "header": [], "body": "[]"}],
            ),
        ],
    )
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    selection_repository = _InMemorySelectionRepository(
        ActiveSelection(workspace_id="ws-1", collection_id="c1")
    )
    use_case = _build_use_case(
        collection_repository=collection_repository, selection_repository=selection_repository
    )

    result = use_case.execute()

    assert len(result.endpoint_outcomes) == 2
    sources = {outcome.endpoint_source for outcome in result.endpoint_outcomes}
    assert sources == {"POST /pets", "GET /pets"}


# --- Request com script manual ------------------------------------------------------


def test_manual_script_is_preserved_and_reflected_in_diff():
    document = _parse(
        "Col",
        [
            _request(
                "Criar pet",
                "r1",
                method="POST",
                url="https://x/pets",
                events=[
                    {
                        "listen": "test",
                        "script": {
                            "exec": [
                                "// comentário manual do usuário",
                                "console.log('não mexa aqui');",
                            ]
                        },
                    }
                ],
                responses=[{"name": "ok", "status": "OK", "code": 201, "header": [], "body": "{}"}],
            )
        ],
    )
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    selection_repository = _InMemorySelectionRepository(
        ActiveSelection(workspace_id="ws-1", collection_id="c1")
    )
    use_case = _build_use_case(
        collection_repository=collection_repository, selection_repository=selection_repository
    )

    result = use_case.execute()

    outcome = result.endpoint_outcomes[0]
    assert "// comentário manual do usuário" in outcome.merged_script
    assert "console.log('não mexa aqui');" in outcome.merged_script
    assert "pm.response.to.have.status(201)" in outcome.merged_script
    # o script gerado "puro" (dentro do bloco) não contém o código manual
    assert "console.log('não mexa aqui');" not in outcome.generated_script.script

    # o documento original em si nunca é alterado
    original_event = document.items[0].events[0]
    assert original_event.exec_lines == (
        "// comentário manual do usuário",
        "console.log('não mexa aqui');",
    )

    # o diff mostra o bloco adicionado, mas o código manual não é reportado
    # como removido — ele foi preservado, não descartado
    assert not any(
        "não mexa aqui" in entry.description for entry in result.diff.entries
    )


# --- Artefatos isolados ----------------------------------------------------------------


def test_artifacts_are_isolated_by_workspace_collection_and_execution(tmp_path):
    document = _parse(
        "Col",
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
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    selection_repository = _InMemorySelectionRepository(
        ActiveSelection(workspace_id="ws-1", collection_id="c1")
    )
    use_case = _build_use_case(
        collection_repository=collection_repository,
        selection_repository=selection_repository,
        id_factory=lambda: "exec-abc",
        artifact_base_path=tmp_path,
    )

    result = use_case.execute()

    assert len(result.artifact_locations) >= 1
    for location in result.artifact_locations:
        assert str(tmp_path / "ws-1" / "c1" / "exec-abc") in location.path


# --- Diff gerado ---------------------------------------------------------------------


def test_diff_reflects_added_managed_block():
    document = _parse("Col", [_request("Ping", "r1")])
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    selection_repository = _InMemorySelectionRepository(
        ActiveSelection(workspace_id="ws-1", collection_id="c1")
    )
    use_case = _build_use_case(
        collection_repository=collection_repository, selection_repository=selection_repository
    )

    result = use_case.execute()

    assert result.diff.has_changes is True


# --- Nenhuma chamada de update ---------------------------------------------------------


def test_no_update_call_is_ever_made():
    document = _parse("Col", [_request("Ping", "r1")])
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    assert not hasattr(collection_repository, "update")

    selection_repository = _InMemorySelectionRepository(
        ActiveSelection(workspace_id="ws-1", collection_id="c1")
    )
    use_case = _build_use_case(
        collection_repository=collection_repository, selection_repository=selection_repository
    )

    use_case.execute()


# --- Idempotência ----------------------------------------------------------------------


def test_generated_scripts_are_identical_across_two_executions():
    document = _parse(
        "Col",
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
    collection_repository = _FakeCollectionRepository(
        (CollectionRef(id="c1", name="Col", workspace_id="ws-1"),), {"c1": document}
    )
    selection_repository = _InMemorySelectionRepository(
        ActiveSelection(workspace_id="ws-1", collection_id="c1")
    )
    use_case = _build_use_case(
        collection_repository=collection_repository, selection_repository=selection_repository
    )

    first = use_case.execute()
    second = use_case.execute()

    first_script = first.endpoint_outcomes[0].generated_script.script
    second_script = second.endpoint_outcomes[0].generated_script.script
    assert first_script == second_script
    assert first.diff.entries == second.diff.entries


# --- _sanitize_filename: nomes de arquivo longos (limite MAX_PATH do Windows) --------


def test_sanitize_filename_within_limit_is_unchanged():
    value = "POST /pets"

    assert _sanitize_filename(value) == "POST_pets"


def test_sanitize_filename_above_limit_is_truncated_with_hash():
    value = "GET " + "/".join(f"segmento-longo-{i}" for i in range(10))
    sanitized_full = value.replace(" ", "_").replace("/", "_")

    result = _sanitize_filename(value)
    prefix, _, hash_suffix = result.rpartition("_")

    assert len(result) <= _MAX_FILENAME_LENGTH
    assert sanitized_full.startswith(prefix)
    assert len(hash_suffix) == 8


def test_sanitize_filename_truncation_is_deterministic():
    value = "POST " + "x" * 200

    assert _sanitize_filename(value) == _sanitize_filename(value)


def test_sanitize_filename_avoids_collision_for_same_long_prefix():
    prefix = "x" * 200
    value_a = f"{prefix}-a"
    value_b = f"{prefix}-b"

    assert _sanitize_filename(value_a) != _sanitize_filename(value_b)
