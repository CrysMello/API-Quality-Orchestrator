"""Caracterização do schema de `result.json` (schema_version "1.2") exatamente
como ele é serializado hoje — antes da Fase 9 do plano Playwright (que
introduz o campo aditivo `tool` e bumpa para "1.3" deliberadamente).

Ver tests/characterization/README.md: se este teste quebrar durante a Fase
9, é esperado — atualize-o conscientemente. Se quebrar em qualquer outro
momento, é uma regressão real.
"""

from datetime import datetime

from api_quality_agent.application.use_cases import persist_execution_result as persist_module
from api_quality_agent.domain.models import ExecutionResult


def _build_result(**overrides: object) -> ExecutionResult:
    defaults: dict[str, object] = {
        "collection_source": "Pets Offline",
        "success": True,
        "exit_code": 0,
        "duration_seconds": 1.5,
        "total_requests": 2,
        "total_assertions": 2,
        "failed_assertions": 0,
        "test_failures": (),
        "infrastructure_failure": None,
        "stdout": "stdout gigante que nunca deve ser persistido",
        "stderr": "",
    }
    defaults.update(overrides)
    return ExecutionResult(**defaults)  # type: ignore[arg-type]


def test_schema_version_is_still_1_2() -> None:
    assert persist_module.EXECUTION_RESULT_SCHEMA_VERSION == "1.2"


def test_serialized_top_level_keys_are_unchanged() -> None:
    serialized = persist_module._serialize(
        _build_result(),
        collection_id="col-1",
        collection_name="Pets Offline",
        started_at=datetime(2026, 8, 4, 10, 0, 0),
        finished_at=datetime(2026, 8, 4, 10, 0, 2),
        workspace_id="ws-1",
        workspace_name="QA Workspace",
    )

    assert set(serialized.keys()) == {
        "schema_version",
        "execution",
        "workspace",
        "collection",
        "summary",
        "test_failures",
        "success",
        "infrastructure_failure",
    }
    assert serialized["schema_version"] == "1.2"
    assert set(serialized["execution"].keys()) == {
        "started_at",
        "finished_at",
        "duration_seconds",
    }
    assert set(serialized["summary"].keys()) == {"requests", "assertions", "passed", "failed"}
    # stdout/stderr nunca são persistidos — contrato de segurança explícito.
    assert "stdout" not in serialized
    assert "stderr" not in serialized


def test_local_file_run_serializes_null_workspace_and_collection() -> None:
    # run --file: sem Workspace/Collection reais do Postman envolvidos.
    serialized = persist_module._serialize(
        _build_result(),
        collection_id=None,
        collection_name=None,
        started_at=datetime(2026, 8, 4, 10, 0, 0),
        finished_at=datetime(2026, 8, 4, 10, 0, 2),
        workspace_id=None,
        workspace_name=None,
    )

    assert serialized["workspace"] == {"id": None, "name": None}
    assert serialized["collection"] == {"id": None, "name": None}
