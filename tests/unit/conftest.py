"""Fixtures compartilhadas pelos testes da CLI (list/generate/version).

Os comandos da CLI montam suas dependências via cli.bootstrap.build_context(),
que sempre aponta para a API real do Postman. Para testar sem rede real, os
nomes importados dentro do módulo bootstrap são substituídos (monkeypatch)
por versões apontadas para o servidor fake e para diretórios temporários —
nenhum código de produção é alterado para viabilizar isso.
"""

import os
import sys
from pathlib import Path

import pytest

from api_quality_agent.adapters.config import FileSelectionRepository
from api_quality_agent.adapters.filesystem import (
    JsonExecutionResultRepository,
    LocalArtifactRepository,
    LocalBackupRepository,
)
from api_quality_agent.adapters.newman import NewmanAdapter
from api_quality_agent.adapters.playwright import PlaywrightAdapter
from api_quality_agent.adapters.postman import PostmanApiClient
from api_quality_agent.cli import bootstrap
from api_quality_agent.domain.models import ActiveSelection

FAKE_NEWMAN_SCRIPT = Path(__file__).resolve().parent.parent / "fake_newman.py"
FAKE_PYTEST_SCRIPT = Path(__file__).resolve().parent.parent / "fake_pytest.py"

FAKE_API_KEY = "PMAK-cli-fake-key-0000000000000000000000"

WORKSPACE_ID = "ws-cli-1"
WORKSPACE_NAME = "QA Workspace"
WORKSPACE_B_ID = "ws-cli-2"
WORKSPACE_B_NAME = "Ops Workspace"
DUPLICATE_WORKSPACE_ID = "ws-cli-1-dup"

COLLECTION_A_ID = "col-cli-a"
COLLECTION_A_NAME = "Pets API"
COLLECTION_B_ID = "col-cli-b"
COLLECTION_B_NAME = "Orders API"
DUPLICATE_COLLECTION_ID = "col-cli-a-dup"


def collection_a_payload() -> dict:
    return {
        "info": {
            "name": COLLECTION_A_NAME,
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            {
                "name": "Criar pet",
                "id": "req-a1",
                "request": {"method": "POST", "url": "https://api.exemplo.com/pets"},
                "response": [
                    {"name": "ok", "status": "Created", "code": 201, "header": [], "body": "{}"}
                ],
            }
        ],
    }


def collection_b_payload() -> dict:
    return {
        "info": {
            "name": COLLECTION_B_NAME,
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            {
                "name": "Listar pedidos",
                "id": "req-b1",
                "request": {"method": "GET", "url": "https://api.exemplo.com/orders"},
                "response": [
                    {"name": "ok", "status": "OK", "code": 200, "header": [], "body": "[]"}
                ],
            }
        ],
    }


def configure_server(
    server,
    *,
    workspaces: list[dict] | None = None,
    multiple_workspaces: bool = False,
    duplicate_workspace_name: bool = False,
    empty_workspaces: bool = False,
    with_collections: bool = True,
    duplicate_name: bool = False,
    empty_collections: bool = False,
) -> None:
    if workspaces is None:
        workspaces = (
            []
            if empty_workspaces
            else [{"id": WORKSPACE_ID, "name": WORKSPACE_NAME}]
        )
        if multiple_workspaces:
            workspaces.append({"id": WORKSPACE_B_ID, "name": WORKSPACE_B_NAME})
        if duplicate_workspace_name:
            workspaces.append({"id": DUPLICATE_WORKSPACE_ID, "name": WORKSPACE_NAME})

    server.set_route("/me", status=200, body={"user": {"id": 1, "username": "qa"}})
    server.set_route("/workspaces", status=200, body={"workspaces": workspaces})

    if not with_collections:
        return

    collections = (
        []
        if empty_collections
        else [
            {"id": COLLECTION_A_ID, "uid": COLLECTION_A_ID, "name": COLLECTION_A_NAME},
            {"id": COLLECTION_B_ID, "uid": COLLECTION_B_ID, "name": COLLECTION_B_NAME},
        ]
    )
    if duplicate_name:
        collections.append(
            {"id": DUPLICATE_COLLECTION_ID, "uid": DUPLICATE_COLLECTION_ID, "name": COLLECTION_A_NAME}
        )
    server.set_route(
        f"/collections?workspace={WORKSPACE_ID}",
        status=200,
        body={"collections": collections},
    )

    server.set_route(
        f"/collections/{COLLECTION_A_ID}",
        status=200,
        body={"collection": collection_a_payload()},
    )
    server.set_route(
        f"/collections/{COLLECTION_B_ID}",
        status=200,
        body={"collection": collection_b_payload()},
    )
    if duplicate_name:
        server.set_route(
            f"/collections/{DUPLICATE_COLLECTION_ID}",
            status=200,
            body={"collection": collection_a_payload()},
        )

    server.set_route(
        f"/collections/{COLLECTION_A_ID}",
        method="PUT",
        status=200,
        body={"collection": {"id": COLLECTION_A_ID, "uid": COLLECTION_A_ID}},
    )
    server.set_route(
        f"/collections/{COLLECTION_B_ID}",
        method="PUT",
        status=200,
        body={"collection": {"id": COLLECTION_B_ID, "uid": COLLECTION_B_ID}},
    )


@pytest.fixture
def cli_env(monkeypatch, tmp_path: Path, postman_test_server):
    monkeypatch.setenv("POSTMAN_API_KEY", FAKE_API_KEY)

    def _fake_client(api_key: str) -> PostmanApiClient:
        return PostmanApiClient(
            api_key,
            base_url=postman_test_server.base_url,
            timeout_seconds=2.0,
            max_retries=0,
        )

    selection_path = tmp_path / "selection.json"
    artifacts_path = tmp_path / "artifacts"
    backups_path = tmp_path / "backups"
    execution_results_path = tmp_path / "artifacts"

    monkeypatch.setattr(bootstrap, "PostmanApiClient", _fake_client)
    monkeypatch.setattr(
        bootstrap, "FileSelectionRepository", lambda: FileSelectionRepository(selection_path)
    )
    monkeypatch.setattr(
        bootstrap, "LocalArtifactRepository", lambda: LocalArtifactRepository(artifacts_path)
    )
    monkeypatch.setattr(
        bootstrap, "LocalBackupRepository", lambda: LocalBackupRepository(backups_path)
    )
    monkeypatch.setattr(
        bootstrap,
        "JsonExecutionResultRepository",
        lambda: JsonExecutionResultRepository(execution_results_path),
    )

    return postman_test_server


@pytest.fixture
def selected_workspace(cli_env, tmp_path: Path):
    repository = FileSelectionRepository(tmp_path / "selection.json")
    repository.save(ActiveSelection(workspace_id=WORKSPACE_ID))
    return WORKSPACE_ID


@pytest.fixture
def read_active_selection(tmp_path: Path):
    def _read() -> ActiveSelection:
        return FileSelectionRepository(tmp_path / "selection.json").load()

    return _read


@pytest.fixture
def offline_env(monkeypatch, tmp_path: Path):
    # Modo "arquivo local": garante explicitamente a ausência da API Key
    # (prova que o fluxo não depende dela) e isola os artefatos gerados.
    monkeypatch.delenv("POSTMAN_API_KEY", raising=False)
    monkeypatch.setattr(
        bootstrap, "LocalArtifactRepository", lambda: LocalArtifactRepository(tmp_path / "artifacts")
    )
    monkeypatch.setattr(
        bootstrap,
        "JsonExecutionResultRepository",
        lambda: JsonExecutionResultRepository(tmp_path / "artifacts"),
    )
    return tmp_path


@pytest.fixture
def fake_newman_offline(offline_env, monkeypatch):
    # Equivalente a fake_newman, mas sem depender de cli_env (que configura
    # POSTMAN_API_KEY/servidor fake do Postman) — usado por testes de
    # `run --file`, que nunca deveriam precisar de nada relacionado ao Postman.
    factory = _RecordingNewmanAdapterFactory()
    monkeypatch.setattr(bootstrap, "NewmanAdapter", factory)
    return factory


class _RecordingNewmanAdapterFactory:
    """Substitui bootstrap.NewmanAdapter: registra o newman_executable
    resolvido (para testar a precedência flag > env > padrão) mas sempre
    executa o processo fake real (tests/fake_newman.py), nunca o Newman de
    verdade nem um mock da chamada."""

    def __init__(self) -> None:
        self.captured_executables: list[str] = []

    def __call__(self, *, newman_executable: str, command_prefix: tuple[str, ...] = ()) -> NewmanAdapter:
        self.captured_executables.append(newman_executable)
        return NewmanAdapter(
            newman_executable=sys.executable, command_prefix=(str(FAKE_NEWMAN_SCRIPT),)
        )


@pytest.fixture
def fake_newman(cli_env, monkeypatch):
    factory = _RecordingNewmanAdapterFactory()
    monkeypatch.setattr(bootstrap, "NewmanAdapter", factory)
    return factory


@pytest.fixture
def fake_pytest_offline(offline_env, monkeypatch):
    # Equivalente a fake_newman_offline, para `run --target playwright`:
    # substitui bootstrap.PlaywrightAdapter por uma versão apontada para o
    # processo fake real (tests/fake_pytest.py), nunca o pytest de verdade
    # nem um mock da chamada.
    def _factory(**_kwargs) -> PlaywrightAdapter:
        return PlaywrightAdapter(
            pytest_executable=sys.executable, command_prefix=(str(FAKE_PYTEST_SCRIPT),)
        )

    monkeypatch.setattr(bootstrap, "PlaywrightAdapter", _factory)
    return _factory


@pytest.fixture
def no_api_key_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("POSTMAN_API_KEY", None)
    return env
