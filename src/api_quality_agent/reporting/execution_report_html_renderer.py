from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from api_quality_agent.reporting.report import (
    Report,
    ReportAssertionResult,
    ReportExecutionSection,
    ReportHttpTransaction,
    ReportInfrastructureFailure,
    ReportTestExecution,
)

_STATUS_LABELS = {
    "infrastructure_failure": "INFRASTRUCTURE FAILURE",
    "passed": "PASSED",
    "failed": "FAILED",
}
_STATUS_ICONS = {"passed": "✓", "failed": "✗", "infrastructure_failure": "⚠"}


def render_execution_report_html(report: Report, *, source_path: str, schema_version: str) -> str:
    # Renderer dedicado ao relatório produzido a partir de um result.json
    # (api-quality-orchestrator report) — layout com cards/barra de progresso,
    # diferente do render_report_html() genérico usado por generate/update.
    # Reaproveita o mesmo Report/ReportExecutionSection do ReportEngine;
    # todo texto vindo do result.json passa por escape() antes de entrar no
    # HTML, e nenhum CSS/JS remoto é referenciado (autocontido, offline).
    execution = report.execution
    status = _status(execution)

    title = _e(report.collection_name or report.collection_id or "Execution Report")
    workspace_display = _e(report.workspace_name) or "N/A"
    collection_display = _e(report.collection_name) or "N/A"

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — API Quality Orchestrator</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <p class="brand">API Quality Orchestrator</p>
  <h1>Execution Report</h1>
  <p class="status status-{status}"><span aria-hidden="true">{_STATUS_ICONS[status]}</span> {_STATUS_LABELS[status]}</p>
  <ul class="header-meta">
    <li><strong>Workspace:</strong> {workspace_display}</li>
    <li><strong>Collection:</strong> {collection_display}</li>
    <li><strong>Data:</strong> {_format_datetime(report.generated_at)}</li>
  </ul>
</header>

<main>
{_render_cards(execution)}
{_render_summary(execution)}
{_render_information(report, execution)}
{_render_failures(execution)}
{_render_tests(execution)}
{_render_metadata(source_path, report.generated_at, schema_version)}
</main>
</body>
</html>"""


def _status(execution: ReportExecutionSection) -> str:
    if execution.infrastructure_failure is not None:
        return "infrastructure_failure"
    return "passed" if execution.success else "failed"


def _render_cards(execution: ReportExecutionSection) -> str:
    passed = _passed(execution)
    cards = [
        ("Requests", execution.total_requests),
        ("Assertions", execution.total_assertions),
        ("Passed", passed),
        ("Failed", execution.failed_assertions),
    ]
    items = "".join(
        f'<div class="card"><p class="card-value">{_e(str(value)) if value is not None else "N/A"}</p>'
        f'<p class="card-label">{_e(label)}</p></div>'
        for label, value in cards
    )
    return f'<section aria-label="Resumo em números"><div class="cards">{items}</div></section>'


def _passed(execution: ReportExecutionSection) -> int | None:
    if execution.total_assertions is None or execution.failed_assertions is None:
        return None
    return execution.total_assertions - execution.failed_assertions


def _render_summary(execution: ReportExecutionSection) -> str:
    total = execution.total_assertions or 0
    failed = execution.failed_assertions or 0
    passed = total - failed
    pass_rate = (passed / total * 100) if total > 0 else 0.0
    return f"""<section aria-label="Resumo estatístico">
  <h2>Resumo</h2>
  <div class="progress" role="progressbar" aria-valuenow="{pass_rate:.0f}" aria-valuemin="0" aria-valuemax="100">
    <div class="progress-bar" style="width:{pass_rate:.1f}%"></div>
  </div>
  <p>{pass_rate:.1f}% das assertions passaram ({passed} de {total}).</p>
</section>"""


def _render_information(report: Report, execution: ReportExecutionSection) -> str:
    rows = [
        ("Workspace", _e(report.workspace_name) or "N/A"),
        ("Collection", _e(report.collection_name) or "N/A"),
        ("Started", _format_datetime(execution.started_at) if execution.started_at else "N/A"),
        ("Finished", _format_datetime(execution.finished_at) if execution.finished_at else "N/A"),
        (
            "Duration",
            f"{execution.duration_seconds:.1f} s" if execution.duration_seconds is not None else "N/A",
        ),
        ("Agent Version", _e(report.agent_version)),
    ]
    rows_html = "".join(f'<tr><th scope="row">{label}</th><td>{value}</td></tr>' for label, value in rows)
    return f'<section aria-label="Informações"><h2>Informações</h2><table>{rows_html}</table></section>'


def _render_failures(execution: ReportExecutionSection) -> str:
    if execution.infrastructure_failure is not None:
        return (
            '<section aria-label="Falhas"><h2>Falhas</h2>'
            f"<p><strong>Falha de infraestrutura:</strong> "
            f"{_e(execution.infrastructure_failure.failure_type)} — "
            f"{_e(execution.infrastructure_failure.message)}</p></section>"
        )
    if execution.test_failures:
        rows = "".join(
            "<tr>"
            f"<td>{_e(failure.request_name or '')}</td>"
            f"<td>{_e(failure.test_name)}</td>"
            f"<td>{_e(failure.error_message)}</td>"
            "</tr>"
            for failure in execution.test_failures
        )
        return (
            '<section aria-label="Falhas"><h2>Falhas</h2>'
            "<table><thead><tr><th>Request</th><th>Teste</th><th>Mensagem</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></section>"
        )
    if not execution.failed_assertions:
        return '<section aria-label="Falhas"><h2>Falhas</h2><p>Nenhuma falha encontrada.</p></section>'
    return (
        '<section aria-label="Falhas"><h2>Falhas</h2>'
        f"<p>{execution.failed_assertions} assertion(s) falharam. "
        "Detalhamento por request/teste não está disponível neste resultado.</p></section>"
    )


# --- P1.2 (integração com ReportEngine): request/response/assertions por --
# --- teste, a partir de HttpTransaction/AssertionResult já persistidos ----
# --- (ver ReportEngine._build_test_executions) — puramente apresentação: --
# --- nenhum PASS/FAIL é decidido aqui, só exibido. Ausente (tests == ())  -
# --- para resultados antigos (sem schema 1.4/1.5) e para Newman, que      -
# --- nunca preenche isto — o restante do relatório continua idêntico.    --


def _render_tests(execution: ReportExecutionSection) -> str:
    if not execution.tests:
        return ""
    # P1.6: correlação por test_id/test_name (mesmo valor em ambos os
    # lados — sempre request.node.name, ver PlaywrightAdapter) feita aqui,
    # na apresentação, com dados que ReportExecutionSection já carrega —
    # nenhuma mudança em ReportEngine/report.py foi necessária.
    failed_test_names = {failure.test_name for failure in execution.test_failures}
    blocks = "".join(
        _render_test_execution(test, has_test_failure=test.test_id in failed_test_names)
        for test in execution.tests
    )
    return f'<section aria-label="Testes"><h2>Testes</h2>{blocks}</section>'


def _render_test_execution(test: ReportTestExecution, *, has_test_failure: bool) -> str:
    status = _test_status(test.assertions, has_test_failure=has_test_failure)
    status_badge = (
        f'<span class="status status-{status}">'
        f'<span aria-hidden="true">{_STATUS_ICONS[status]}</span> {_STATUS_LABELS[status]}</span>'
        if status is not None
        else '<span class="status">N/A</span>'
    )
    transactions_html = (
        "".join(
            _render_transaction(transaction, index, len(test.transactions))
            for index, transaction in enumerate(test.transactions, start=1)
        )
        if test.transactions
        else "<p>Nenhuma transação HTTP registrada para este teste.</p>"
    )
    assertions_html = _render_assertions(test.assertions)
    trace_html = _render_trace_link(test)
    evidence_failures_html = _render_evidence_failures(test)
    return f"""<div class="test-block">
  <div class="test-header"><h3>{_e(test.test_id) or "(sem test_id)"}</h3>{status_badge}</div>
  {trace_html}
  {evidence_failures_html}
  {transactions_html}
  {assertions_html}
</div>"""


def _render_evidence_failures(test: ReportTestExecution) -> str:
    # P1.5 (infrastructure failure das evidências): NUNCA apresentado como
    # uma assertion (bloco visualmente distinto, nunca dentro de
    # _render_assertions) — o resultado FUNCIONAL do teste (status_badge/
    # assertions acima) já foi decidido e permanece intacto independente
    # disto; isto só sinaliza que uma evidência (hoje, o Trace) não pôde
    # ser capturada/mascarada/persistida com segurança.
    if not test.evidence_failures:
        return ""
    items = "".join(
        f"<li>{_e(failure.message)}</li>" for failure in test.evidence_failures
    )
    return (
        '<div class="evidence-failures">'
        '<p class="evidence-failures-title">'
        '<span aria-hidden="true">⚠</span> Infraestrutura de evidências</p>'
        f"<ul>{items}</ul>"
        "</div>"
    )


def _render_trace_link(test: ReportTestExecution) -> str:
    # P1.3 (Trace em falha): ReportEngine nunca gera o trace, só apresenta
    # a referência já persistida (ver TraceArtifact) — ausente (None) para
    # um teste que passou, ou para um resultado antigo sem trace algum.
    if test.trace is None:
        return ""
    if not test.trace.available:
        # P1.4 (hardening): arquivo referenciado não existe mais (movido/
        # apagado) — nunca um link morto silencioso; a referência em si
        # (test_id) continua visível, nunca apagada.
        return (
            '<p class="trace-link trace-link-unavailable">'
            "Trace indisponível (arquivo não encontrado).</p>"
        )
    href = _e(_file_uri(test.trace.path))
    return f'<p class="trace-link">Trace disponível: <a href="{href}">Ver Trace</a></p>'


def _file_uri(path: str) -> str:
    try:
        return Path(path).as_uri()
    except ValueError:
        # Caminho não-absoluto (nunca esperado — ReportEngine sempre
        # resolve para absoluto antes de chegar aqui) — nunca quebra a
        # renderização do relatório por causa disso, só perde a
        # clicabilidade do link.
        return path


def _test_status(
    assertions: tuple[ReportAssertionResult, ...], *, has_test_failure: bool
) -> str | None:
    # Agregação de exibição apenas: cada AssertionResult.status/TestFailure
    # já foi decidido durante a execução real (JUnit do pytest / o `assert`
    # original do teste gerado) — aqui só resume um selo pro teste, nunca
    # uma nova comparação/validação. Regra de prioridade (P1.6):
    #   1. há um TestFailure para este test_id -> FAILED, mesmo sem
    #      nenhuma assertion (ex.: erro de transporte, erro de coleta —
    #      nunca chegou a existir uma assertion pra registrar);
    #   2. sem TestFailure, mas alguma assertion FAILED -> FAILED;
    #   3. sem TestFailure, todas as assertions PASSED -> PASSED;
    #   4. nem TestFailure nem assertions -> N/A (None) — informação
    #      insuficiente, nunca inventado.
    # evidence_failures NUNCA entra nesta decisão (nem aqui, nem no
    # parâmetro): uma falha de infraestrutura de evidência é apresentada à
    # parte (ver _render_evidence_failures) e nunca pode transformar um
    # teste aprovado em reprovado, nem ser tratada como TestFailure.
    if has_test_failure:
        return "failed"
    if not assertions:
        return None
    return "failed" if any(a.status == "FAILED" for a in assertions) else "passed"


def _render_transaction(transaction: ReportHttpTransaction, index: int, total: int) -> str:
    label = f" #{index}" if total > 1 else ""
    request_body = (
        f"<pre>{_e(transaction.request_body)}</pre>"
        if transaction.request_body
        else "<p><em>No request body</em></p>"
    )
    response_body = (
        f"<pre>{_e(transaction.response_body)}</pre>"
        if transaction.response_body
        else "<p><em>No response body</em></p>"
    )
    return f"""<div class="transaction">
  <h4>Request{label} — O que foi enviado</h4>
  <table>
    <tr><th scope="row">Method</th><td>{_e(transaction.method)}</td></tr>
    <tr><th scope="row">URL</th><td>{_e(transaction.url)}</td></tr>
  </table>
  {_render_headers("Request Headers", transaction.request_headers)}
  {request_body}
  <h4>Response{label} — O que a API devolveu</h4>
  <table>
    <tr><th scope="row">HTTP Status</th><td>{transaction.response_status}</td></tr>
  </table>
  {_render_headers("Response Headers", transaction.response_headers)}
  {response_body}
</div>"""


def _render_headers(title: str, headers: tuple[Any, ...]) -> str:
    if not headers:
        return f"<p><strong>{_e(title)}:</strong> <em>Nenhum header registrado.</em></p>"
    rows = "".join(
        f"<tr><th scope='row'>{_e(header.name)}</th><td>{_e(header.value)}</td></tr>"
        for header in headers
    )
    return f"<p><strong>{_e(title)}:</strong></p><table>{rows}</table>"


def _render_assertions(assertions: tuple[ReportAssertionResult, ...]) -> str:
    if not assertions:
        return "<h4>Assertions — O que foi validado</h4><p>Nenhuma assertion registrada para este teste.</p>"
    items = "".join(_render_assertion(assertion) for assertion in assertions)
    return f"<h4>Assertions — O que foi validado</h4>{items}"


def _render_assertion(assertion: ReportAssertionResult) -> str:
    passed = assertion.status == "PASSED"
    icon = "✓" if passed else "✗"
    status_class = "passed" if passed else "failed"
    return f"""<div class="assertion assertion-{status_class}">
  <p class="assertion-name"><span aria-hidden="true">{icon}</span> {_e(assertion.name)}</p>
  <table>
    <tr><th scope="row">Expected</th><td>{_render_value(assertion.expected)}</td></tr>
    <tr><th scope="row">Actual</th><td>{_render_value(assertion.actual)}</td></tr>
    <tr><th scope="row">Precision</th><td>{_e(assertion.precision)}</td></tr>
  </table>
  <p class="assertion-reason"><strong>Reason:</strong> {_e(assertion.reason)}</p>
</div>"""


def _render_value(value: object) -> str:
    # expected/actual podem ser escalares (int/bool/None/str) ou, quando o
    # valor original não era um escalar simples, uma string JSON já
    # serializada por PlaywrightAdapter (_masked_scalar) — nenhuma decisão
    # de diff estruturado é tomada aqui (fora de escopo deste bloco), só
    # apresentação legível do que já está persistido.
    if value is None:
        return "<em>null</em>"
    return _e(str(value))


def _render_metadata(source_path: str, generated_at: datetime, schema_version: str) -> str:
    rows = [
        ("Arquivo de origem", _e(source_path)),
        ("Data de geração", _format_datetime(generated_at)),
        ("Schema", _e(schema_version)),
        ("Formato", "HTML"),
    ]
    rows_html = "".join(f'<tr><th scope="row">{label}</th><td>{value}</td></tr>' for label, value in rows)
    return f'<section aria-label="Metadados"><h2>Metadados</h2><table>{rows_html}</table></section>'


def _format_datetime(value: datetime) -> str:
    return escape(value.strftime("%Y-%m-%d %H:%M:%S"))


def _e(value: str | None) -> str:
    return escape(value) if value else ""


_CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 0;
  background: #f7f7f8; color: #1a1a1a; }
@media (prefers-color-scheme: dark) { body { background: #16171a; color: #eaeaea; } }
header { padding: 1.5rem; background: #ffffff; border-bottom: 1px solid #e0e0e0; }
@media (prefers-color-scheme: dark) { header { background: #1f2023; border-color: #333; } }
.brand { margin: 0; font-weight: 600; color: #6b6b6b; text-transform: uppercase; font-size: 0.8rem;
  letter-spacing: 0.05em; }
h1 { margin: 0.2rem 0 0.8rem; font-size: 1.6rem; }
.status { display: inline-block; padding: 0.4rem 0.9rem; border-radius: 999px; font-weight: 700;
  font-size: 0.95rem; }
.status-passed { background: #dcfce7; color: #166534; }
.status-failed { background: #fee2e2; color: #991b1b; }
.status-infrastructure_failure { background: #fef3c7; color: #92400e; }
@media (prefers-color-scheme: dark) {
  .status-passed { background: #14532d; color: #bbf7d0; }
  .status-failed { background: #7f1d1d; color: #fecaca; }
  .status-infrastructure_failure { background: #78350f; color: #fde68a; }
}
.header-meta { list-style: none; margin: 1rem 0 0; padding: 0; display: flex; gap: 1.5rem; flex-wrap: wrap; }
main { max-width: 900px; margin: 0 auto; padding: 1.5rem; }
section { background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 1rem 1.25rem;
  margin-bottom: 1.25rem; }
@media (prefers-color-scheme: dark) { section { background: #1f2023; border-color: #333; } }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 1rem; }
.card { text-align: center; padding: 0.75rem; border-radius: 6px; background: #f2f2f3; }
@media (prefers-color-scheme: dark) { .card { background: #2a2b2f; } }
.card-value { font-size: 1.6rem; font-weight: 700; margin: 0; }
.card-label { margin: 0.2rem 0 0; color: #6b6b6b; font-size: 0.85rem; }
.progress { background: #e5e5e5; border-radius: 999px; height: 12px; overflow: hidden; }
@media (prefers-color-scheme: dark) { .progress { background: #333; } }
.progress-bar { background: #16a34a; height: 100%; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #eee; }
@media (prefers-color-scheme: dark) { th, td { border-color: #333; } }
.test-block { border: 1px solid #e0e0e0; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.25rem; }
@media (prefers-color-scheme: dark) { .test-block { border-color: #333; } }
.test-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  margin-bottom: 0.75rem; }
.test-header h3 { margin: 0; font-size: 1.05rem; font-family: ui-monospace, Consolas, monospace; }
.transaction { background: #f9f9fa; border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 0.75rem; }
@media (prefers-color-scheme: dark) { .transaction { background: #202124; } }
.transaction h4 { margin: 0.6rem 0 0.4rem; font-size: 0.9rem; text-transform: uppercase;
  letter-spacing: 0.03em; color: #6b6b6b; }
.transaction h4:first-child { margin-top: 0; }
.transaction pre { white-space: pre-wrap; word-break: break-word; background: #eee; padding: 0.5rem;
  border-radius: 4px; margin: 0.3rem 0; }
@media (prefers-color-scheme: dark) { .transaction pre { background: #111; } }
.assertion { border-left: 4px solid #999; padding: 0.5rem 0.75rem; margin: 0.5rem 0; border-radius: 4px;
  background: #f9f9fa; }
@media (prefers-color-scheme: dark) { .assertion { background: #202124; } }
.assertion-passed { border-left-color: #16a34a; }
.assertion-failed { border-left-color: #dc2626; }
.assertion-name { font-weight: 700; margin: 0 0 0.4rem; }
.assertion-reason { margin: 0.4rem 0 0; color: #444; }
@media (prefers-color-scheme: dark) { .assertion-reason { color: #ccc; } }
.trace-link { background: #fef3c7; border-radius: 6px; padding: 0.5rem 0.75rem; margin: 0 0 0.75rem; }
@media (prefers-color-scheme: dark) { .trace-link { background: #78350f; } }
.trace-link-unavailable { background: #f2f2f3; color: #6b6b6b; }
@media (prefers-color-scheme: dark) { .trace-link-unavailable { background: #2a2b2f; color: #aaa; } }
.evidence-failures { border: 1px dashed #d97706; border-radius: 6px; padding: 0.5rem 0.75rem;
  margin: 0 0 0.75rem; background: #fffbeb; }
@media (prefers-color-scheme: dark) { .evidence-failures { background: #3a2e0f; border-color: #d97706; } }
.evidence-failures-title { font-weight: 700; margin: 0 0 0.3rem; color: #92400e; }
@media (prefers-color-scheme: dark) { .evidence-failures-title { color: #fde68a; } }
.evidence-failures ul { margin: 0; padding-left: 1.25rem; }
"""
