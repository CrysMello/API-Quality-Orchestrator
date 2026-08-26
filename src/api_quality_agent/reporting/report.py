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
class ReportHttpTransactionHeader:
    name: str
    value: str


@dataclass(frozen=True)
class ReportHttpTransaction:
    # Espelha domain.models.HttpTransaction — nunca reaproveitado
    # diretamente aqui (mesmo padrão já usado por ReportTestFailure/
    # ReportInfrastructureFailure): o renderer HTML nunca importa domínio.
    # Os valores já chegam mascarados (PlaywrightAdapter) — este módulo
    # nunca tenta recuperar nem decidir o que é secret.
    method: str
    url: str
    request_headers: tuple[ReportHttpTransactionHeader, ...]
    request_body: str | None
    response_status: int
    response_headers: tuple[ReportHttpTransactionHeader, ...]
    response_body: str | None


@dataclass(frozen=True)
class ReportAssertionResult:
    # Espelha domain.models.AssertionResult — expected/actual preservam o
    # tipo/formato já persistido em result.json (P1.2 do bloco de
    # ReportEngine: nenhuma decisão nova sobre diff estruturado aqui).
    name: str
    expected: object
    actual: object
    status: str
    precision: str
    reason: str


@dataclass(frozen=True)
class ReportTraceArtifact:
    # Espelha domain.models.TraceArtifact — só a referência (nunca o
    # conteúdo binário do .zip). `path` aqui já é o caminho ABSOLUTO
    # resolvido pelo ReportEngine (ver _resolve_trace_href), pronto para
    # virar um link clicável no HTML independente de onde o próprio
    # report.html for escrito (--output pode apontar para outro
    # diretório).
    test_id: str
    path: str


@dataclass(frozen=True)
class ReportTestExecution:
    # Agrupamento por test_id (P1.2 do bloco de ReportEngine): correlaciona
    # test_id -> HttpTransaction(s) -> AssertionResult(s) exatamente como já
    # gravado em result.json — nunca reordenado, nunca misturado com outro
    # test_id. `transactions`/`assertions` preservam a ordem original de
    # execução.
    test_id: str
    transactions: tuple[ReportHttpTransaction, ...]
    assertions: tuple[ReportAssertionResult, ...]
    # None quando o teste não gerou trace (passou, ou a suíte foi gerada
    # antes da P1.3) — nunca inventado; ReportEngine nunca gera um trace,
    # só apresenta o que já foi persistido (ver TraceArtifact).
    trace: ReportTraceArtifact | None = None


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
    # Só existe a partir do schema 1.4/1.5 (http_transactions/
    # assertion_results) — vazio para resultados antigos e para Newman
    # (que nunca preenche esses campos), nunca inventado.
    tests: tuple[ReportTestExecution, ...] = ()


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
