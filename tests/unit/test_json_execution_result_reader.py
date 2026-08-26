import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api_quality_agent.adapters.filesystem import JsonExecutionResultReader
from api_quality_agent.domain.exceptions import (
    InputFileNotFoundError,
    InvalidExecutionResultError,
    InvalidJsonError,
    UnsupportedExecutionResultSchemaError,
)

_STARTED_AT = "2026-07-20T10:35:12+00:00"
_FINISHED_AT = "2026-07-20T10:35:46+00:00"


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_payload_1_1(**overrides) -> dict:
    payload = {
        "schema_version": "1.1",
        "execution": {
            "started_at": _STARTED_AT,
            "finished_at": _FINISHED_AT,
            "duration_seconds": 34.1,
        },
        "workspace": {"id": "ws-1", "name": "QA Workspace"},
        "collection": {"id": "col-1", "name": "PetStore"},
        "summary": {"requests": 28, "assertions": 312, "passed": 309, "failed": 3},
        "success": False,
        "infrastructure_failure": None,
    }
    payload.update(overrides)
    return payload


def _valid_payload_1_2(**overrides) -> dict:
    payload = _valid_payload_1_1(schema_version="1.2")
    payload["test_failures"] = [
        {"request_name": "Criar pet", "test_name": "Status 201", "error_message": "boom"},
    ]
    payload.update(overrides)
    return payload


def _valid_payload_1_3(**overrides) -> dict:
    payload = _valid_payload_1_2(schema_version="1.3")
    payload["summary"] = {**payload["summary"], "skipped": 2}
    payload.update(overrides)
    return payload


def _valid_payload_1_4(**overrides) -> dict:
    payload = _valid_payload_1_3(schema_version="1.4")
    payload["http_transactions"] = [
        {
            "method": "GET",
            "url": "https://api.exemplo.com/users",
            "request_headers": {"Accept": "application/json"},
            "request_body": None,
            "response_status": 200,
            "response_headers": {"content-type": "application/json"},
            "response_body": '{"items": []}',
        },
    ]
    payload.update(overrides)
    return payload


def _valid_payload_1_5(**overrides) -> dict:
    payload = _valid_payload_1_4(schema_version="1.5")
    payload["http_transactions"][0]["test_id"] = "test_get_users_success"
    payload["assertion_results"] = [
        {
            "test_id": "test_get_users_success",
            "name": "HTTP status",
            "expected": 200,
            "actual": 200,
            "status": "PASSED",
            "precision": "EXACT",
            "reason": "Status HTTP 200 documentado explicitamente (evidência: contract).",
        },
    ]
    payload.update(overrides)
    return payload


def _valid_payload_1_6(**overrides) -> dict:
    payload = _valid_payload_1_5(schema_version="1.6")
    payload["trace_artifacts"] = [
        {
            "type": "playwright-trace",
            "test_id": "test_get_users_success",
            "path": "traces/00-test_get_users_success.zip",
        },
    ]
    payload.update(overrides)
    return payload


def _valid_payload_1_7(**overrides) -> dict:
    payload = _valid_payload_1_6(schema_version="1.7")
    payload["evidence_failures"] = [
        {
            "type": "evidence_persistence_failed",
            "source": "playwright_trace",
            "test_id": "test_post_orders_fail",
            "message": "Falha ao mascarar o Trace; artefato não persistido por segurança.",
        },
    ]
    payload.update(overrides)
    return payload


def _valid_payload_1_0() -> dict:
    # Schema 1.0: sem schema_version e sem workspace no arquivo.
    return {
        "execution": {
            "started_at": _STARTED_AT,
            "finished_at": _FINISHED_AT,
            "duration_seconds": 34.1,
        },
        "collection": {"id": "col-1", "name": "PetStore"},
        "summary": {"requests": 28, "assertions": 312, "passed": 309, "failed": 3},
        "success": True,
        "infrastructure_failure": None,
    }


# --- Leitura: schema 1.1 ----------------------------------------------------------------


def test_read_schema_1_1_populates_workspace(tmp_path):
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_1())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.schema_version == "1.1"
    assert record.workspace_id == "ws-1"
    assert record.workspace_name == "QA Workspace"
    assert record.collection_id == "col-1"
    assert record.collection_name == "PetStore"
    assert record.total_requests == 28
    assert record.total_assertions == 312
    assert record.failed_assertions == 3
    assert record.passed_assertions == 309
    assert record.success is False
    assert record.infrastructure_failure is None
    assert record.started_at == datetime(2026, 7, 20, 10, 35, 12, tzinfo=timezone.utc)
    assert record.finished_at == datetime(2026, 7, 20, 10, 35, 46, tzinfo=timezone.utc)
    assert record.source_path == str(path)
    assert record.test_failures == ()
    assert record.skipped_tests == 0
    assert record.http_transactions == ()


# --- Leitura: schema 1.2 ----------------------------------------------------------------


def test_read_schema_1_2_populates_test_failures(tmp_path):
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_2())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.schema_version == "1.2"
    assert len(record.test_failures) == 1
    assert record.test_failures[0].request_name == "Criar pet"
    assert record.test_failures[0].test_name == "Status 201"
    assert record.test_failures[0].error_message == "boom"


def test_read_schema_1_2_defaults_skipped_tests_to_zero(tmp_path):
    # "summary.skipped" só existe a partir do 1.3 — um result.json 1.2
    # legado (sem essa chave) precisa continuar sendo lido, com 0.
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_2())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.skipped_tests == 0


# --- Leitura: schema 1.3 ----------------------------------------------------------------


def test_read_schema_1_3_populates_skipped_tests(tmp_path):
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_3())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.schema_version == "1.3"
    assert record.skipped_tests == 2
    # Demais campos continuam populados normalmente (regressão do que já
    # existia no 1.2).
    assert len(record.test_failures) == 1
    assert record.total_requests == 28
    assert record.total_assertions == 312
    assert record.failed_assertions == 3


def test_read_schema_1_3_with_zero_skipped_tests(tmp_path):
    payload = _valid_payload_1_3()
    payload["summary"]["skipped"] = 0
    path = _write(tmp_path / "run_x" / "result.json", payload)
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.skipped_tests == 0


def test_read_schema_1_3_defaults_http_transactions_to_empty(tmp_path):
    # "http_transactions" só existe a partir do 1.4 — um result.json 1.3
    # legado (sem essa chave) precisa continuar sendo lido, com tupla vazia.
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_3())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.http_transactions == ()


# --- Leitura: schema 1.4 ----------------------------------------------------------------


def test_read_schema_1_4_populates_http_transactions(tmp_path):
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_4())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.schema_version == "1.4"
    assert len(record.http_transactions) == 1
    transaction = record.http_transactions[0]
    assert transaction.method == "GET"
    assert transaction.url == "https://api.exemplo.com/users"
    assert transaction.request_body is None
    assert transaction.response_status == 200
    assert transaction.response_body == '{"items": []}'
    assert {h.name: h.value for h in transaction.request_headers} == {
        "Accept": "application/json"
    }
    assert {h.name: h.value for h in transaction.response_headers} == {
        "content-type": "application/json"
    }
    # Demais campos continuam populados normalmente (regressão do que já
    # existia no 1.3).
    assert record.skipped_tests == 2
    assert len(record.test_failures) == 1


def test_read_schema_1_4_with_no_http_transactions(tmp_path):
    payload = _valid_payload_1_4(http_transactions=[])
    path = _write(tmp_path / "run_x" / "result.json", payload)
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.http_transactions == ()


def test_read_schema_1_4_defaults_http_transaction_test_id_and_assertion_results(tmp_path):
    # "http_transactions[].test_id" e "assertion_results" só existem a
    # partir do 1.5 — um result.json 1.4 legado precisa continuar sendo
    # lido, com "" e tupla vazia respectivamente.
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_4())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.http_transactions[0].test_id == ""
    assert record.assertion_results == ()


# --- Leitura: schema 1.5 ----------------------------------------------------------------


def test_read_schema_1_5_populates_test_id_and_assertion_results(tmp_path):
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_5())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.schema_version == "1.5"
    assert record.http_transactions[0].test_id == "test_get_users_success"
    assert len(record.assertion_results) == 1
    assertion_result = record.assertion_results[0]
    assert assertion_result.test_id == "test_get_users_success"
    assert assertion_result.name == "HTTP status"
    assert assertion_result.expected == 200
    assert assertion_result.actual == 200
    assert assertion_result.status == "PASSED"
    assert assertion_result.precision == "EXACT"
    assert "evidência: contract" in assertion_result.reason
    # Correlação: mesmo test_id na transação e na assertion.
    assert record.http_transactions[0].test_id == assertion_result.test_id


def test_read_schema_1_5_with_no_assertion_results(tmp_path):
    payload = _valid_payload_1_5(assertion_results=[])
    path = _write(tmp_path / "run_x" / "result.json", payload)
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.assertion_results == ()


def test_read_schema_1_5_defaults_trace_artifacts_to_empty(tmp_path):
    # "trace_artifacts" só existe a partir do schema 1.6 — ausente em 1.5,
    # tratado como tupla vazia, nunca inventado.
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_5())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.trace_artifacts == ()


# --- Leitura: schema 1.6 (P1.3 — Trace em falha) ----------------------------------------


def test_read_schema_1_6_populates_trace_artifacts(tmp_path):
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_6())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.schema_version == "1.6"
    assert len(record.trace_artifacts) == 1
    artifact = record.trace_artifacts[0]
    assert artifact.type == "playwright-trace"
    assert artifact.test_id == "test_get_users_success"
    assert artifact.path == "traces/00-test_get_users_success.zip"
    # Correlação: mesmo test_id na transação, na assertion e no trace.
    assert record.http_transactions[0].test_id == artifact.test_id
    assert record.assertion_results[0].test_id == artifact.test_id


def test_read_schema_1_6_with_no_trace_artifacts(tmp_path):
    payload = _valid_payload_1_6(trace_artifacts=[])
    path = _write(tmp_path / "run_x" / "result.json", payload)
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.trace_artifacts == ()


def test_read_schema_1_6_defaults_evidence_failures_to_empty(tmp_path):
    # "evidence_failures" só existe a partir do schema 1.7 — ausente em
    # 1.6, tratado como tupla vazia, nunca inventado (item 15).
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_6())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.evidence_failures == ()


# --- Leitura: schema 1.7 (P1.5 — infrastructure failure das evidências) -----------------


def test_read_schema_1_7_populates_evidence_failures(tmp_path):
    from api_quality_agent.domain.models import InfrastructureFailureType

    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_7())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.schema_version == "1.7"
    assert len(record.evidence_failures) == 1
    failure = record.evidence_failures[0]
    assert failure.failure_type == InfrastructureFailureType.EVIDENCE_PERSISTENCE_FAILED
    assert failure.source == "playwright_trace"
    assert failure.test_id == "test_post_orders_fail"
    assert "mascarar" in failure.message.lower()


def test_read_schema_1_7_with_no_evidence_failures(tmp_path):
    payload = _valid_payload_1_7(evidence_failures=[])
    path = _write(tmp_path / "run_x" / "result.json", payload)
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.evidence_failures == ()


# --- Leitura: schema 1.0 (retrocompatibilidade) ----------------------------------------------------------------


def test_read_schema_1_0_defaults_workspace_to_none(tmp_path):
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_0())
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.schema_version == "1.0"
    assert record.workspace_id is None
    assert record.workspace_name is None
    assert record.collection_id == "col-1"
    assert record.success is True
    assert record.skipped_tests == 0
    assert record.http_transactions == ()


# --- Falha de infraestrutura persistida ----------------------------------------------------------------


def test_read_with_infrastructure_failure(tmp_path):
    payload = _valid_payload_1_1(
        infrastructure_failure={"type": "executable_not_found", "message": "Newman não encontrado."}
    )
    path = _write(tmp_path / "run_x" / "result.json", payload)
    reader = JsonExecutionResultReader(tmp_path)

    record = reader.read(path=path)

    assert record.infrastructure_failure is not None
    assert record.infrastructure_failure.failure_type.value == "executable_not_found"
    assert record.infrastructure_failure.message == "Newman não encontrado."


# --- Erros de leitura ----------------------------------------------------------------


def test_read_missing_file_raises_input_file_not_found(tmp_path):
    reader = JsonExecutionResultReader(tmp_path)

    with pytest.raises(InputFileNotFoundError):
        reader.read(path=tmp_path / "nao-existe" / "result.json")


def test_read_invalid_json_raises_invalid_json_error(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("isto não é json", encoding="utf-8")
    reader = JsonExecutionResultReader(tmp_path)

    with pytest.raises(InvalidJsonError):
        reader.read(path=path)


def test_read_unsupported_schema_version_is_rejected(tmp_path):
    path = _write(tmp_path / "run_x" / "result.json", _valid_payload_1_1(schema_version="9.9"))
    reader = JsonExecutionResultReader(tmp_path)

    with pytest.raises(UnsupportedExecutionResultSchemaError):
        reader.read(path=path)


@pytest.mark.parametrize("missing_key", ["execution", "collection", "summary", "success"])
def test_read_missing_required_field_raises_invalid_execution_result(tmp_path, missing_key):
    payload = _valid_payload_1_1()
    del payload[missing_key]
    path = _write(tmp_path / "run_x" / "result.json", payload)
    reader = JsonExecutionResultReader(tmp_path)

    with pytest.raises(InvalidExecutionResultError):
        reader.read(path=path)


def test_read_wrong_type_raises_invalid_execution_result(tmp_path):
    payload = _valid_payload_1_1(success="not-a-boolean")
    path = _write(tmp_path / "run_x" / "result.json", payload)
    reader = JsonExecutionResultReader(tmp_path)

    with pytest.raises(InvalidExecutionResultError):
        reader.read(path=path)


def test_read_non_object_json_raises_invalid_execution_result(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    reader = JsonExecutionResultReader(tmp_path)

    with pytest.raises(InvalidExecutionResultError):
        reader.read(path=path)


# --- Descoberta automática (find_latest) ----------------------------------------------------------------


def test_find_latest_returns_none_when_base_path_does_not_exist(tmp_path):
    reader = JsonExecutionResultReader(tmp_path / "nao-existe")

    assert reader.find_latest() is None


def test_find_latest_returns_none_when_no_results_exist(tmp_path):
    reader = JsonExecutionResultReader(tmp_path)

    assert reader.find_latest() is None


def test_find_latest_ignores_incompatible_files(tmp_path):
    (tmp_path / "run_x").mkdir()
    (tmp_path / "run_x" / "not-a-result.txt").write_text("irrelevante", encoding="utf-8")
    reader = JsonExecutionResultReader(tmp_path)

    assert reader.find_latest() is None


def test_find_latest_selects_the_most_recently_modified_result(tmp_path):
    older = _write(tmp_path / "run_a" / "result.json", _valid_payload_1_1())
    newer = _write(tmp_path / "run_b" / "result.json", _valid_payload_1_1())

    # Garante uma diferença de mtime perceptível entre os dois arquivos.
    import os
    import time

    time.sleep(0.05)
    now = time.time() + 10
    os.utime(newer, (now, now))

    reader = JsonExecutionResultReader(tmp_path)

    assert reader.find_latest() == newer
    assert reader.find_latest() != older
