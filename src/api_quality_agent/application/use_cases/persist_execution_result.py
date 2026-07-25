import json
from datetime import datetime
from typing import Any

from api_quality_agent.domain.models import ExecutionResult, ExecutionResultLocation
from api_quality_agent.ports.outbound import ExecutionResultRepository

# "1.0" (sem schema_version/workspace no arquivo), "1.1" (schema_version +
# workspace, aditivo) e "1.2" (test_failures, aditivo) são as versões que
# api-quality-orchestrator report sabe ler — ver JsonExecutionResultReader. Mudanças
# de schema são sempre aditivas; nenhum campo existente é removido ou
# renomeado.
EXECUTION_RESULT_SCHEMA_VERSION = "1.2"


class PersistExecutionResultUseCase:
    def __init__(self, execution_result_repository: ExecutionResultRepository) -> None:
        self._execution_result_repository = execution_result_repository

    def execute(
        self,
        result: ExecutionResult,
        *,
        collection_id: str | None,
        collection_name: str | None,
        started_at: datetime,
        finished_at: datetime,
        workspace_id: str | None = None,
        workspace_name: str | None = None,
    ) -> ExecutionResultLocation:
        content = json.dumps(
            _serialize(
                result,
                collection_id=collection_id,
                collection_name=collection_name,
                started_at=started_at,
                finished_at=finished_at,
                workspace_id=workspace_id,
                workspace_name=workspace_name,
            ),
            indent=2,
            ensure_ascii=False,
        )
        return self._execution_result_repository.save(content=content)


def _serialize(
    result: ExecutionResult,
    *,
    collection_id: str | None,
    collection_name: str | None,
    started_at: datetime,
    finished_at: datetime,
    workspace_id: str | None,
    workspace_name: str | None,
) -> dict[str, Any]:
    # Serialização explícita e estruturada: nunca stdout/stderr brutos, nunca
    # a Collection completa — só os campos já expostos pelo domínio, usados
    # como entrada oficial de `api-quality-orchestrator report`. collection_id/
    # workspace_* ficam None quando a execução veio de um arquivo local
    # (run --file) — não há Workspace/Collection do Postman envolvidos.
    infrastructure_failure = result.infrastructure_failure
    return {
        "schema_version": EXECUTION_RESULT_SCHEMA_VERSION,
        "execution": {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": result.duration_seconds,
        },
        "workspace": {
            "id": workspace_id,
            "name": workspace_name,
        },
        "collection": {
            "id": collection_id,
            "name": collection_name,
        },
        "summary": {
            "requests": result.total_requests,
            "assertions": result.total_assertions,
            "passed": result.total_assertions - result.failed_assertions,
            "failed": result.failed_assertions,
        },
        "test_failures": [
            {
                "request_name": failure.request_name,
                "test_name": failure.test_name,
                "error_message": failure.error_message,
            }
            for failure in result.test_failures
        ],
        "success": result.success,
        "infrastructure_failure": (
            {
                "type": infrastructure_failure.failure_type.value,
                "message": infrastructure_failure.message,
            }
            if infrastructure_failure is not None
            else None
        ),
    }
