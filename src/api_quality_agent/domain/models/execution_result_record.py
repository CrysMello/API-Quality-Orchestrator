from dataclasses import dataclass
from datetime import datetime

from api_quality_agent.domain.models.assertion_result import AssertionResult
from api_quality_agent.domain.models.http_transaction import HttpTransaction
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
    # Só existe a partir do schema 1.3 (ver JsonExecutionResultReader) —
    # default 0 preserva toda construção existente (schema 1.0/1.1/1.2 lido,
    # e os testes de ReportEngine/HTML renderer que constroem este record
    # direto, sem passar por esse campo).
    skipped_tests: int = 0
    # P1.2: só existe a partir do schema 1.4 — mesmo raciocínio aditivo de
    # skipped_tests (default preserva toda construção existente).
    http_transactions: tuple[HttpTransaction, ...] = ()
    # P1.1 (detalhamento de assertions): só existe a partir do schema 1.5 —
    # mesmo raciocínio aditivo.
    assertion_results: tuple[AssertionResult, ...] = ()

    @property
    def passed_assertions(self) -> int:
        return self.total_assertions - self.failed_assertions
