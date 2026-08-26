"""Caracterização do schema de `result.json` (schema_version "1.5") exatamente
como ele é serializado hoje.

"1.5" foi introduzida na P1.1 seguinte — detalhamento de assertions (aditivo:
`assertion_results` + `http_transactions[].test_id`, pra correlação test_id
-> request/response -> assertions) — bumps anteriores foram "1.4" (P1.2,
http_transactions), "1.3" (P1.1/skipped_tests, summary.skipped) e "1.2"
(test_failures). A Fase 9 do plano Playwright ainda vai introduzir o campo
aditivo `tool` e bumpar de novo, deliberadamente.

Ver tests/characterization/README.md: se este teste quebrar durante uma
dessas mudanças planejadas, é esperado — atualize-o conscientemente. Se
quebrar em qualquer outro momento, é uma regressão real.
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


def test_schema_version_is_now_1_5() -> None:
    assert persist_module.EXECUTION_RESULT_SCHEMA_VERSION == "1.5"


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
        "http_transactions",
        "assertion_results",
        "success",
        "infrastructure_failure",
    }
    assert serialized["schema_version"] == "1.5"
    assert set(serialized["execution"].keys()) == {
        "started_at",
        "finished_at",
        "duration_seconds",
    }
    assert set(serialized["summary"].keys()) == {
        "requests",
        "assertions",
        "passed",
        "failed",
        "skipped",
    }
    assert serialized["http_transactions"] == []
    assert serialized["assertion_results"] == []
    # stdout/stderr nunca são persistidos — contrato de segurança explícito.
    assert "stdout" not in serialized
    assert "stderr" not in serialized


def test_http_transaction_keys_are_unchanged() -> None:
    from api_quality_agent.domain.models import HttpTransaction, HttpTransactionHeader

    result = _build_result(
        http_transactions=(
            HttpTransaction(
                test_id="test_get_users_success",
                method="GET",
                url="https://api.exemplo.com/users",
                request_headers=(HttpTransactionHeader(name="Accept", value="application/json"),),
                request_body=None,
                response_status=200,
                response_headers=(HttpTransactionHeader(name="content-type", value="application/json"),),
                response_body="{}",
            ),
        )
    )
    serialized = persist_module._serialize(
        result,
        collection_id="col-1",
        collection_name="Pets Offline",
        started_at=datetime(2026, 8, 4, 10, 0, 0),
        finished_at=datetime(2026, 8, 4, 10, 0, 2),
        workspace_id="ws-1",
        workspace_name="QA Workspace",
    )

    assert len(serialized["http_transactions"]) == 1
    assert set(serialized["http_transactions"][0].keys()) == {
        "test_id",
        "method",
        "url",
        "request_headers",
        "request_body",
        "response_status",
        "response_headers",
        "response_body",
    }


def test_assertion_result_keys_are_unchanged() -> None:
    from api_quality_agent.domain.models import AssertionResult

    result = _build_result(
        assertion_results=(
            AssertionResult(
                test_id="test_get_users_success",
                name="HTTP status",
                expected=200,
                actual=200,
                status="PASSED",
                precision="EXACT",
                reason="Status HTTP 200 documentado explicitamente (evidência: contract).",
            ),
        )
    )
    serialized = persist_module._serialize(
        result,
        collection_id="col-1",
        collection_name="Pets Offline",
        started_at=datetime(2026, 8, 4, 10, 0, 0),
        finished_at=datetime(2026, 8, 4, 10, 0, 2),
        workspace_id="ws-1",
        workspace_name="QA Workspace",
    )

    assert len(serialized["assertion_results"]) == 1
    assert set(serialized["assertion_results"][0].keys()) == {
        "test_id",
        "name",
        "expected",
        "actual",
        "status",
        "precision",
        "reason",
    }


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
