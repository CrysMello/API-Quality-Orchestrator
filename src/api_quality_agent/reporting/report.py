from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ReportEndpointSummary:
    source: str
    succeeded: bool
    test_count: int
    schema_warning_count: int
    strategy_warning_count: int
    error: str | None


@dataclass(frozen=True)
class ReportDiffEntry:
    change_type: str
    category: str
    target: str
    risk: str
    description: str


@dataclass(frozen=True)
class ReportDiffSection:
    entries: tuple[ReportDiffEntry, ...]
    has_changes: bool
    has_removals: bool
    has_high_risk_changes: bool


@dataclass(frozen=True)
class ReportUpdateSection:
    # attempted=False representa "não houve tentativa de atualização" —
    # nesse caso approved/updated/demais campos ficam None.
    attempted: bool
    approved: bool | None
    updated: bool | None
    dry_run: bool | None
    denial_reason: str | None
    backup_created: bool | None
    document_hash: str | None
    status_code: int | None


@dataclass(frozen=True)
class ReportTestFailure:
    request_name: str | None
    test_name: str
    error_message: str


@dataclass(frozen=True)
class ReportInfrastructureFailure:
    failure_type: str
    message: str


@dataclass(frozen=True)
class ReportExecutionSection:
    # executed=False representa "Newman não foi executado nesta operação" —
    # o relatório deve poder ser gerado normalmente mesmo assim.
    executed: bool
    success: bool | None
    exit_code: int | None
    duration_seconds: float | None
    total_requests: int | None
    total_assertions: int | None
    failed_assertions: int | None
    test_failures: tuple[ReportTestFailure, ...]
    infrastructure_failure: ReportInfrastructureFailure | None
    # Só preenchidos quando o relatório vem de um result.json persistido
    # (api-quality-orchestrator report) — o ExecutionResult "ao vivo" do fluxo
    # generate/update/run nunca teve início/fim absolutos, só duration.
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class Report:
    execution_id: str
    generated_at: datetime
    duration_seconds: float
    mode: str
    source: str
    workspace_id: str | None
    workspace_name: str | None
    collection_id: str | None
    collection_name: str | None
    selection_origin: str
    endpoints: tuple[ReportEndpointSummary, ...]
    analysis_warnings: tuple[str, ...]
    execution_warnings: tuple[str, ...]
    diff: ReportDiffSection
    update: ReportUpdateSection
    execution: ReportExecutionSection
    artifacts: tuple[str, ...]
    agent_version: str
