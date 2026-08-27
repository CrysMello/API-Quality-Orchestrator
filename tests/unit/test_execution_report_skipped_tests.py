"""P1.7: apresentação do contador AGREGADO de skipped_tests no relatório
HTML — o dado já existia corretamente em ExecutionResult/
ExecutionResultRecord/result.json (ver JsonExecutionResultReader), mas se
perdia entre ReportEngine e o HTML porque ReportExecutionSection não tinha
nenhum campo equivalente.

Este arquivo cobre exclusivamente essa apresentação — nunca reconstrói
quais test_ids especificamente foram pulados (essa informação não existe
hoje no ExecutionResult), nunca mistura skipped com passed/failed de
assertions, e nunca altera o significado de `success`.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from api_quality_agent.adapters.filesystem import JsonExecutionResultReader
from api_quality_agent.application.orchestration import CollectionGenerationResult
from api_quality_agent.application.use_cases import PersistExecutionResultUseCase
from api_quality_agent.domain.models import (
    AssertionResult,
    DiffResult,
    ExecutionContext,
    ExecutionMode,
    ExecutionResult,
    ExecutionResultLocation,
    ExecutionResultRecord,
    InfrastructureFailure,
    InfrastructureFailureType,
    TestFailure,
)
from api_quality_agent.parsers import PostmanCollectionParser
from api_quality_agent.reporting import ReportEngine, render_execution_report_html

_STARTED_AT = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 26, 10, 0, 2, tzinfo=timezone.utc)


def _record(**overrides) -> ExecutionResultRecord:
    defaults = dict(
        source_path="artifacts/run_20260826_100000/result.json",
        schema_version="1.7",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
        duration_seconds=2.0,
        workspace_id="ws-1",
        workspace_name="QA Workspace",
        collection_id="col-1",
        collection_name="Users API",
        total_requests=1,
        total_assertions=1,
        failed_assertions=0,
        success=True,
        infrastructure_failure=None,
        test_failures=(),
    )
    defaults.update(overrides)
    return ExecutionResultRecord(**defaults)


def _assertion(**overrides) -> AssertionResult:
    defaults = dict(
        test_id="test_post_users_success",
        name="HTTP status",
        expected=201,
        actual=201,
        status="PASSED",
        precision="EXACT",
        reason="O contrato define HTTP 201 como resposta esperada.",
    )
    defaults.update(overrides)
    return AssertionResult(**defaults)


def _evidence_failure(**overrides) -> InfrastructureFailure:
    defaults = dict(
        failure_type=InfrastructureFailureType.EVIDENCE_PERSISTENCE_FAILED,
        message="Falha ao mascarar o Trace; artefato não persistido por segurança.",
        source="playwright_trace",
        test_id="test_post_users_success",
    )
    defaults.update(overrides)
    return InfrastructureFailure(**defaults)


def _test_failure(**overrides) -> TestFailure:
    defaults = dict(
        request_name=None,
        test_name="test_post_orders_success",
        error_message="assert 500 == 201",
    )
    defaults.update(overrides)
    return TestFailure(**defaults)


def _render(record: ExecutionResultRecord) -> str:
    report = ReportEngine().generate_from_execution_summary(record)
    return render_execution_report_html(
        report, source_path=record.source_path, schema_version=record.schema_version
    )


def _card_value(html: str, label: str) -> str:
    # Extrai o valor exibido no card <label> (Requests/Assertions/Passed/
    # Failed/Skipped seguem todos o mesmo layout gerado por _render_cards).
    marker = f'<p class="card-label">{label}</p>'
    assert marker in html, f"card '{label}' não encontrado no HTML"
    before = html[: html.index(marker)]
    value_open = before.rindex('<p class="card-value">') + len('<p class="card-value">')
    value_close = before.index("</p>", value_open)
    return before[value_open:value_close]


def _skipped_card_value(html: str) -> str:
    return _card_value(html, "Skipped")


# --- A. skipped_tests = 0 ----------------------------------------------------


def test_a_skipped_zero_renders_zero_not_na():
    record = _record(skipped_tests=0)

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert report.execution.skipped_tests == 0
    assert _skipped_card_value(html) == "0"
    # Nenhum teste skipped individual é inventado.
    assert report.execution.tests == ()


# --- B. skipped_tests = 1 ----------------------------------------------------


def test_b_skipped_one_renders_one():
    record = _record(skipped_tests=1)

    html = _render(record)

    assert _skipped_card_value(html) == "1"


# --- C. skipped_tests > 1 ----------------------------------------------------


def test_c_skipped_three_renders_three():
    record = _record(skipped_tests=3)

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert report.execution.skipped_tests == 3
    assert _skipped_card_value(html) == "3"


# --- D. combinação passed=10 / failed=2 / skipped=3 --------------------------


def test_d_passed_failed_and_skipped_all_appear_correctly_together():
    record = _record(
        total_assertions=12,
        failed_assertions=2,
        skipped_tests=3,
        success=False,
    )

    html = _render(record)

    # Passed (assertions) = total_assertions - failed_assertions = 10.
    assert _card_value(html, "Passed") == "10"
    assert _card_value(html, "Failed") == "2"
    assert _skipped_card_value(html) == "3"


# --- E. skipped não é contado como passed ------------------------------------


def test_e_skipped_is_never_counted_as_passed():
    # total_assertions/failed_assertions não têm nenhuma relação com
    # skipped_tests — mudar skipped não pode mudar o card "Passed".
    baseline_html = _render(_record(total_assertions=5, failed_assertions=1, skipped_tests=0))
    skipped_html = _render(_record(total_assertions=5, failed_assertions=1, skipped_tests=4))

    assert _card_value(baseline_html, "Passed") == _card_value(skipped_html, "Passed") == "4"


# --- F. skipped não é contado como failed ------------------------------------


def test_f_skipped_is_never_counted_as_failed():
    baseline_html = _render(_record(total_assertions=5, failed_assertions=1, skipped_tests=0))
    skipped_html = _render(_record(total_assertions=5, failed_assertions=1, skipped_tests=4))

    assert _card_value(baseline_html, "Failed") == _card_value(skipped_html, "Failed") == "1"


# --- G. round-trip completo: Persist -> result.json -> Reader -> ReportEngine


class _RealFileRepository:
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def save(self, *, content: str) -> ExecutionResultLocation:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / "result.json"
        path.write_text(content, encoding="utf-8")
        return ExecutionResultLocation(path=str(path))


def test_g_skipped_survives_the_full_pipeline_round_trip(tmp_path):
    run_dir = tmp_path / "artifacts" / "run_skipped"
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=True,
        exit_code=0,
        duration_seconds=3.0,
        total_requests=2,
        total_assertions=2,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        skipped_tests=3,
    )
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))

    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="Sanity Suite P1.7",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
        workspace_id="ws-1",
        workspace_name="QA",
    )

    result_json_path = Path(location.path)
    reader = JsonExecutionResultReader(run_dir.parent)
    record = reader.read(path=result_json_path)
    assert record.skipped_tests == 3

    report = ReportEngine().generate_from_execution_summary(record)
    assert report.execution.skipped_tests == 3

    html = render_execution_report_html(
        report, source_path=record.source_path, schema_version=record.schema_version
    )
    assert _skipped_card_value(html) == "3"


# --- H. evidence_failures não altera o contador de skipped -------------------


def test_h_evidence_failures_do_not_affect_the_skipped_counter():
    record = _record(
        skipped_tests=2,
        evidence_failures=(_evidence_failure(),),
    )

    html = _render(record)

    assert _skipped_card_value(html) == "2"
    assert "Infraestrutura de evidências" in html


# --- I. test_failures não altera o contador de skipped -----------------------


def test_i_test_failures_do_not_affect_the_skipped_counter():
    record = _record(
        skipped_tests=2,
        test_failures=(_test_failure(),),
        success=False,
    )

    html = _render(record)

    assert _skipped_card_value(html) == "2"
    assert 'class="status status-failed"' in html


# --- J. execução ausente (fluxo generate/update) => "N/A", nunca 0 ---------


def _generation_result() -> CollectionGenerationResult:
    document = {
        "info": {
            "name": "Col",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [{"name": "Ping", "request": {"method": "GET", "url": "https://x/y"}}],
    }
    parsed = PostmanCollectionParser().parse_text(json.dumps(document))
    execution_context = ExecutionContext.create(
        mode=ExecutionMode.ONLINE,
        source="postman",
        workspace_id="ws-1",
        collection_id="c1",
        collection_name="Col",
        id_factory=lambda: "exec-1",
    )
    return CollectionGenerationResult(
        execution_context=execution_context,
        analysis_warnings=(),
        dependencies=(),
        endpoint_outcomes=(),
        diff=DiffResult(entries=()),
        original_document=parsed,
        modified_document=parsed,
        artifact_locations=(),
    )


def test_j_no_execution_result_shows_na_never_zero_for_skipped():
    # execution_result=None (fluxo generate/update, Newman/Playwright nunca
    # rodou nesta operação) — skipped_tests deve ficar None -> "N/A" no
    # card, nunca 0 inventado.
    report = ReportEngine().generate(_generation_result(), execution_result=None)

    assert report.execution.executed is False
    assert report.execution.skipped_tests is None
