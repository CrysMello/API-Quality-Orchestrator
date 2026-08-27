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
    # P1.5 (infrastructure failure das evidências): source/test_id só são
    # preenchidos quando esta instância representa a falha de uma
    # EVIDÊNCIA específica (ver ReportTestExecution.evidence_failures) —
    # a falha "de execução inteira" (ReportExecutionSection.
    # infrastructure_failure, singular) continua com os dois em None,
    # exatamente como antes.
    source: str | None = None
    test_id: str | None = None


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
    # P2.1 (evidência HTTP): query parameters passados explicitamente no
    # call site gerado — nunca reparseados a partir de `url` (que já
    # contém a query string real). Mesmo formato de
    # ReportHttpTransactionHeader (par nome/valor); vazio quando o
    # resultado é anterior ao P2.1 ou quando a request não teve nenhum.
    query_parameters: tuple[ReportHttpTransactionHeader, ...] = ()


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
    # resolvido pelo ReportEngine (ver _build_report_trace), pronto para
    # virar um link clicável no HTML independente de onde o próprio
    # report.html for escrito (--output pode apontar para outro
    # diretório).
    test_id: str
    path: str
    # P1.4 (hardening): True só quando o arquivo referenciado por `path`
    # existe fisicamente no momento em que o relatório é gerado (checado
    # por ReportEngine) — False para uma referência histórica cujo
    # arquivo foi movido/apagado depois. Nunca decide apagar a referência
    # em si (preserva a informação histórica), só evita apresentar um link
    # clicável para algo que não existe.
    available: bool = True


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
    # P1.5 (infrastructure failure das evidências): vazio no caso comum
    # (evidência capturada/persistida sem problema) — nunca confundido com
    # `assertions` (resultado FUNCIONAL do teste): isto é sempre um
    # problema de INFRAESTRUTURA de captura de evidência, apresentado
    # separadamente pelo renderer.
    evidence_failures: tuple[ReportInfrastructureFailure, ...] = ()


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
    # P1.7: contador AGREGADO de testes pulados (ver ExecutionResult.
    # skipped_tests) — None só quando não houve execução (executed=False);
    # quando há execução, é sempre um int (0 inclusive, nunca inventado).
    # Nunca correlacionado a nenhum test_id individual: essa informação
    # não existe hoje no ExecutionResult, então não é reconstruída aqui.
    skipped_tests: int | None = None


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
