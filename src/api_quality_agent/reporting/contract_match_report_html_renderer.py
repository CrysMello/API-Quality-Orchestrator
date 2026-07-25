from html import escape

from api_quality_agent.domain.models import ContractValidationIssue, MatchStatus
from api_quality_agent.reporting.contract_match_report import ContractMatchEntry, ContractMatchReport

# Renderer dedicado ao relatório de correspondência de contrato — mesma
# convenção de execution_report_html_renderer.py (autocontido, dark-mode
# aware, sem CSS/JS remoto). Faz sentido ter versão HTML aqui pelo mesmo
# motivo do relatório de execução: escaneabilidade visual rápida de
# status (MATCHED/NOT_FOUND/AMBIGUOUS) por várias linhas, o que o JSON
# sozinho não oferece.

_STATUS_LABELS = {
    MatchStatus.MATCHED: "MATCHED",
    MatchStatus.NOT_FOUND: "NOT FOUND",
    MatchStatus.AMBIGUOUS: "AMBIGUOUS",
}


def render_contract_match_report_html(report: ContractMatchReport) -> str:
    title = f"Contract Match Report — {escape(report.source_file)}"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <p class="brand">API Quality Orchestrator</p>
  <h1>Contract Match Report</h1>
  <p class="source">Contrato: {escape(report.source_file)}</p>
</header>
<main>
{_render_summary(report)}
{_render_table(report)}
{_render_validation_issues(report)}
</main>
</body>
</html>"""


def _render_summary(report: ContractMatchReport) -> str:
    cards = [
        ("Endpoints", report.summary.total),
        ("Matched", report.summary.matched),
        ("Not found", report.summary.not_found),
        ("Ambiguous", report.summary.ambiguous),
    ]
    items = "".join(
        f'<div class="card"><p class="card-value">{value}</p><p class="card-label">{escape(label)}</p></div>'
        for label, value in cards
    )
    return f'<section aria-label="Resumo"><div class="cards">{items}</div></section>'


def _render_table(report: ContractMatchReport) -> str:
    if not report.entries:
        return '<section aria-label="Correspondências"><h2>Correspondências</h2><p>Nenhum endpoint declarado.</p></section>'

    rows = "".join(_render_row(entry) for entry in report.entries)
    return f"""<section aria-label="Correspondências">
  <h2>Correspondências</h2>
  <table>
    <thead>
      <tr><th>Status</th><th>Método</th><th>Path</th><th>Aba / Candidatos</th><th>Diagnósticos</th></tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</section>"""


def _render_row(entry: ContractMatchEntry) -> str:
    status_class = entry.status.value.lower()
    status_label = _STATUS_LABELS[entry.status]
    detail = ""
    diagnostics = ""
    if entry.status is MatchStatus.MATCHED:
        detail = escape(entry.sheet or "")
        if entry.validation_issues:
            diagnostics = f"{len(entry.validation_issues)}"
    elif entry.status is MatchStatus.AMBIGUOUS:
        detail = ", ".join(escape(sheet) for sheet in entry.candidate_sheets)
        if entry.candidate_validation_issues:
            diagnostics = ", ".join(
                f"{escape(candidate.sheet)}: {len(candidate.issues)}"
                for candidate in entry.candidate_validation_issues
            )
    return (
        f'<tr><td><span class="status status-{status_class}">{status_label}</span></td>'
        f"<td>{escape(entry.method)}</td><td><code>{escape(entry.canonical_path)}</code></td>"
        f"<td>{detail}</td><td>{diagnostics}</td></tr>"
    )


def _render_validation_issues(report: ContractMatchReport) -> str:
    if not report.validation_issues:
        return ""

    rows = "".join(_render_issue_row(issue) for issue in report.validation_issues)
    return f"""<section aria-label="Diagnósticos de validação">
  <h2>Diagnósticos de validação</h2>
  <table>
    <thead>
      <tr><th>Severidade</th><th>Aba</th><th>Linha</th><th>Campo</th><th>Mensagem</th></tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</section>"""


def _render_issue_row(issue: ContractValidationIssue) -> str:
    row_label = str(issue.row_number) if issue.row_number is not None else ""
    field_label = escape(issue.field) if issue.field is not None else ""
    return (
        f'<tr><td><span class="severity severity-{escape(issue.severity)}">{escape(issue.severity)}</span></td>'
        f"<td>{escape(issue.sheet)}</td><td>{row_label}</td><td>{field_label}</td>"
        f"<td>{escape(issue.message)}</td></tr>"
    )


_CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 0;
  background: #f7f7f8; color: #1a1a1a; }
@media (prefers-color-scheme: dark) { body { background: #16171a; color: #eaeaea; } }
header { padding: 1.5rem; background: #ffffff; border-bottom: 1px solid #e0e0e0; }
@media (prefers-color-scheme: dark) { header { background: #1f2023; border-color: #333; } }
.brand { margin: 0; font-weight: 600; color: #6b6b6b; text-transform: uppercase; font-size: 0.8rem;
  letter-spacing: 0.05em; }
h1 { margin: 0.2rem 0 0.4rem; font-size: 1.6rem; }
.source { margin: 0; color: #6b6b6b; }
main { max-width: 900px; margin: 0 auto; padding: 1.5rem; }
section { background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 1rem 1.25rem;
  margin-bottom: 1.25rem; overflow-x: auto; }
@media (prefers-color-scheme: dark) { section { background: #1f2023; border-color: #333; } }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 1rem; }
.card { text-align: center; padding: 0.75rem; border-radius: 6px; background: #f2f2f3; }
@media (prefers-color-scheme: dark) { .card { background: #2a2b2f; } }
.card-value { font-size: 1.6rem; font-weight: 700; margin: 0; }
.card-label { margin: 0.2rem 0 0; color: #6b6b6b; font-size: 0.85rem; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #eee; }
@media (prefers-color-scheme: dark) { th, td { border-color: #333; } }
code { font-size: 0.85em; }
.status { display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px; font-weight: 700;
  font-size: 0.8rem; }
.status-matched { background: #dcfce7; color: #166534; }
.status-not_found { background: #fee2e2; color: #991b1b; }
.status-ambiguous { background: #fef3c7; color: #92400e; }
@media (prefers-color-scheme: dark) {
  .status-matched { background: #14532d; color: #bbf7d0; }
  .status-not_found { background: #7f1d1d; color: #fecaca; }
  .status-ambiguous { background: #78350f; color: #fde68a; }
}
.severity { display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px; font-weight: 700;
  font-size: 0.8rem; }
.severity-error { background: #fee2e2; color: #991b1b; }
.severity-warning { background: #fef3c7; color: #92400e; }
@media (prefers-color-scheme: dark) {
  .severity-error { background: #7f1d1d; color: #fecaca; }
  .severity-warning { background: #78350f; color: #fde68a; }
}
"""
