from dataclasses import dataclass
from datetime import datetime

from api_quality_agent.domain.models.infrastructure_failure import InfrastructureFailure
from api_quality_agent.domain.models.test_failure import TestFailure


@dataclass(frozen=True)
class ExecutionResultRecord:
    # Representa um result.json já lido, validado e desserializado — nunca o
    # ExecutionResult "ao vivo" de uma execução do Newman (esse não tem
    # source_path/schema_version/workspace, e carrega stdout/stderr brutos
    # que este registro nunca persiste nem expõe).
    source_path: str
    schema_version: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    workspace_id: str | None
    workspace_name: str | None
    collection_id: str | None
    collection_name: str | None
    total_requests: int
    total_assertions: int
    failed_assertions: int
    success: bool
    infrastructure_failure: InfrastructureFailure | None
    test_failures: tuple[TestFailure, ...]

    @property
    def passed_assertions(self) -> int:
        return self.total_assertions - self.failed_assertions
