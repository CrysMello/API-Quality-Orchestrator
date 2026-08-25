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
