import json
from datetime import datetime, timezone

import pytest

from api_quality_agent.application.use_cases import PersistExecutionResultUseCase
from api_quality_agent.domain.models import (
    ExecutionResult,
    ExecutionResultLocation,
    InfrastructureFailure,
    InfrastructureFailureType,
    TestFailure,
)

_STARTED_AT = datetime(2026, 7, 20, 10, 35, 12, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 7, 20, 10, 35, 46, tzinfo=timezone.utc)


class _CapturingRepository:
    def __init__(self) -> None:
        self.captured_content: str | None = None

    def save(self, *, content: str) -> ExecutionResultLocation:
        self.captured_content = content
        return ExecutionResultLocation(path="artifacts/run_fake/result.json")


def _success_result(**overrides) -> ExecutionResult:
    defaults = dict(
        collection_source="/tmp/whatever.json",
        success=True,
        exit_code=0,
        duration_seconds=34.1,
        total_requests=28,
        total_assertions=312,
        failed_assertions=3,
        test_failures=(
            TestFailure(request_name="Criar pet", test_name="Status 201", error_message="boom"),
        ),
        infrastructure_failure=None,
        stdout="stdout sensível que não deve ser persistido",
        stderr="stderr sensível que não deve ser persistido",
    )
    defaults.update(overrides)
    return ExecutionResult(**defaults)


def test_persisted_json_has_expected_structure():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result()

    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        workspace_id="ws-1",
        workspace_name="QA Workspace",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert location.path == "artifacts/run_fake/result.json"
    payload = json.loads(repository.captured_content)
    assert payload == {
        "schema_version": "1.3",
        "execution": {
            "started_at": _STARTED_AT.isoformat(),
            "finished_at": _FINISHED_AT.isoformat(),
            "duration_seconds": 34.1,
        },
        "workspace": {"id": "ws-1", "name": "QA Workspace"},
        "collection": {"id": "col-1", "name": "PetStore"},
        "summary": {
            "requests": 28,
            "assertions": 312,
            "passed": 309,
            "failed": 3,
            "skipped": 0,
        },
        "test_failures": [
            {"request_name": "Criar pet", "test_name": "Status 201", "error_message": "boom"},
        ],
        "success": True,
        "infrastructure_failure": None,
    }


# --- P1.1: persistência de skipped_tests ----------------------------------------------------


def test_skipped_tests_is_persisted_correctly():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(skipped_tests=4)

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["summary"]["skipped"] == 4


def test_skipped_tests_zero_is_persisted_correctly():
    # skipped_tests=0 é o default de ExecutionResult (Newman nunca tem
    # skipped) — precisa ser persistido como 0 explícito, nunca omitido.
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(skipped_tests=0)

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["summary"]["skipped"] == 0
    assert "skipped" in payload["summary"]


def test_skipped_tests_does_not_affect_other_summary_fields():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(skipped_tests=7)

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["summary"]["requests"] == 28
    assert payload["summary"]["assertions"] == 312
    assert payload["summary"]["passed"] == 309
    assert payload["summary"]["failed"] == 3


def test_persisted_json_workspace_is_null_when_not_provided():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result()

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["workspace"] == {"id": None, "name": None}


def test_infrastructure_failure_is_serialized_as_structured_object():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(
        success=False,
        total_requests=0,
        total_assertions=0,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=InfrastructureFailure(
            failure_type=InfrastructureFailureType.EXECUTABLE_NOT_FOUND,
            message="Executável do Newman não encontrado.",
        ),
    )

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["infrastructure_failure"] == {
        "type": "executable_not_found",
        "message": "Executável do Newman não encontrado.",
    }


@pytest.mark.parametrize("field", ["execution", "collection", "summary"])
def test_persisted_json_never_contains_raw_stdout_or_stderr(field):
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result()

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert "stdout" not in repository.captured_content
    assert "stderr" not in repository.captured_content
    assert "sensível" not in repository.captured_content


def test_persisted_json_never_contains_the_full_collection_document():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result()

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert set(payload.keys()) == {
        "schema_version",
        "execution",
        "workspace",
        "collection",
        "summary",
        "test_failures",
        "success",
        "infrastructure_failure",
    }
