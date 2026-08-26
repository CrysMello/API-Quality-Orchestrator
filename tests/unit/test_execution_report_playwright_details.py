"""P1.2 do bloco de ReportEngine: integração de http_transactions/
assertion_results (Playwright) no relatório HTML — ReportEngine é
puramente apresentação (nunca decide PASS/FAIL, nunca recalcula
expected/actual, nunca mascara por conta própria: os dados já chegam
prontos do result.json).

Cobre exatamente os 17 casos pedidos: ReportEngine com HttpTransaction,
ReportEngine com AssertionResult, request/response com e sem body,
assertion PASS/FAIL, expected/actual, precision, reason, múltiplas
assertions, múltiplas transações, correlação por test_id, compatibilidade
com resultado antigo (sem http_transactions / sem assertion_results) e
Newman continuando a funcionar.
"""

from datetime import datetime, timezone
from pathlib import Path

from api_quality_agent.domain.models import (
    AssertionResult,
    ExecutionResultRecord,
    HttpTransaction,
    HttpTransactionHeader,
    TraceArtifact,
)
from api_quality_agent.reporting import ReportEngine, render_execution_report_html

_STARTED_AT = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 26, 10, 0, 2, tzinfo=timezone.utc)


def _record(**overrides) -> ExecutionResultRecord:
    defaults = dict(
        source_path="artifacts/run_20260826_100000/result.json",
        schema_version="1.5",
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


def _transaction(**overrides) -> HttpTransaction:
    defaults = dict(
        test_id="test_post_users_success",
        method="POST",
        url="https://api.exemplo.com/users",
        request_headers=(HttpTransactionHeader(name="Content-Type", value="application/json"),),
        request_body='{"role": "admin"}',
        response_status=201,
        response_headers=(HttpTransactionHeader(name="content-type", value="application/json"),),
        response_body='{"id": "u-1"}',
    )
    defaults.update(overrides)
    return HttpTransaction(**defaults)


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


def _trace_artifact(**overrides) -> TraceArtifact:
    defaults = dict(
        type="playwright-trace",
        test_id="test_post_users_success",
        path="traces/00-test_post_users_success.zip",
    )
    defaults.update(overrides)
    return TraceArtifact(**defaults)


def _render(record: ExecutionResultRecord) -> str:
    report = ReportEngine().generate_from_execution_summary(record)
    return render_execution_report_html(
        report, source_path=record.source_path, schema_version=record.schema_version
    )


# --- 1. ReportEngine com HttpTransaction ------------------------------------


def test_report_engine_groups_http_transaction_under_its_test_id():
    record = _record(http_transactions=(_transaction(),))

    report = ReportEngine().generate_from_execution_summary(record)

    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert test.test_id == "test_post_users_success"
    assert len(test.transactions) == 1
    assert test.transactions[0].method == "POST"
    assert test.transactions[0].url == "https://api.exemplo.com/users"


# --- 2. ReportEngine com AssertionResult ------------------------------------


def test_report_engine_groups_assertion_result_under_its_test_id():
    record = _record(assertion_results=(_assertion(),))

    report = ReportEngine().generate_from_execution_summary(record)

    test = report.execution.tests[0]
    assert len(test.assertions) == 1
    assert test.assertions[0].name == "HTTP status"


# --- 3/4. Request com e sem body ---------------------------------------------


def test_html_shows_request_body_when_present():
    record = _record(http_transactions=(_transaction(request_body='{"role": "admin"}'),))

    html = _render(record)

    # html.escape() converte aspas em &quot; — o conteúdo textual (chave e
    # valor) continua presente, só a sintaxe JSON literal muda de forma.
    assert "role" in html
    assert "admin" in html
    assert "No request body" not in html


def test_html_shows_no_request_body_message_when_absent():
    record = _record(http_transactions=(_transaction(request_body=None),))

    html = _render(record)

    assert "No request body" in html


# --- 5/6. Response com e sem body --------------------------------------------


def test_html_shows_response_body_when_present():
    record = _record(http_transactions=(_transaction(response_body='{"id": "u-1"}'),))

    html = _render(record)

    assert "u-1" in html
    assert "No response body" not in html


def test_html_shows_no_response_body_message_when_absent():
    record = _record(http_transactions=(_transaction(response_body=None),))

    html = _render(record)

    assert "No response body" in html


# --- 7/8. Assertion PASS / FAIL ------------------------------------------------


def test_html_marks_a_passed_assertion():
    record = _record(assertion_results=(_assertion(status="PASSED"),))

    html = _render(record)

    assert "assertion-passed" in html


def test_html_marks_a_failed_assertion():
    record = _record(
        assertion_results=(_assertion(status="FAILED", expected=201, actual=500),),
        success=False,
        failed_assertions=1,
    )

    html = _render(record)

    assert "assertion-failed" in html


# --- 9. Expected / actual -----------------------------------------------------


def test_html_shows_expected_and_actual_values():
    record = _record(assertion_results=(_assertion(expected=201, actual=500, status="FAILED"),))

    html = _render(record)

    assert "Expected" in html
    assert "Actual" in html
    assert "201" in html
    assert "500" in html


def test_html_shows_null_for_none_expected_or_actual_without_fabricating_a_value():
    record = _record(
        assertion_results=(
            _assertion(name="required_field:id", expected="presente", actual=None, status="FAILED"),
        )
    )

    html = _render(record)

    assert "<em>null</em>" in html


# --- 10. Precision -------------------------------------------------------------


def test_html_shows_precision():
    record = _record(assertion_results=(_assertion(precision="DERIVED"),))

    html = _render(record)

    assert "DERIVED" in html


# --- 11. Reason ------------------------------------------------------------


def test_html_shows_reason():
    record = _record(
        assertion_results=(
            _assertion(reason="O contrato define HTTP 201 como resposta esperada."),
        )
    )

    html = _render(record)

    assert "O contrato define HTTP 201 como resposta esperada." in html


# --- 12. Múltiplas assertions ---------------------------------------------


def test_html_shows_multiple_assertions_for_the_same_test_in_order():
    record = _record(
        assertion_results=(
            _assertion(name="HTTP status", expected=201, actual=201, status="PASSED"),
            _assertion(name="Content-Type", expected="application/json", actual="application/json", status="PASSED"),
        )
    )

    html = _render(record)

    assert html.index("HTTP status") < html.index("Content-Type")


def test_report_engine_preserves_the_order_of_multiple_assertions():
    record = _record(
        assertion_results=(
            _assertion(name="HTTP status"),
            _assertion(name="Content-Type"),
            _assertion(name="json_schema"),
        )
    )

    report = ReportEngine().generate_from_execution_summary(record)

    names = [a.name for a in report.execution.tests[0].assertions]
    assert names == ["HTTP status", "Content-Type", "json_schema"]


# --- 13. Múltiplas transações -------------------------------------------------


def test_report_engine_preserves_the_order_of_multiple_transactions_for_the_same_test():
    record = _record(
        http_transactions=(
            _transaction(method="POST", url="https://api.exemplo.com/users"),
            _transaction(method="GET", url="https://api.exemplo.com/users/u-1"),
        )
    )

    report = ReportEngine().generate_from_execution_summary(record)

    methods = [t.method for t in report.execution.tests[0].transactions]
    assert methods == ["POST", "GET"]


def test_html_numbers_multiple_transactions_of_the_same_test():
    record = _record(
        http_transactions=(
            _transaction(method="POST", url="https://api.exemplo.com/users"),
            _transaction(method="GET", url="https://api.exemplo.com/users/u-1"),
        )
    )

    html = _render(record)

    assert "Request #1" in html
    assert "Request #2" in html


# --- 14. Correlação correta por test_id ---------------------------------------


def test_correlation_never_mixes_assertions_from_different_tests():
    record = _record(
        http_transactions=(
            _transaction(test_id="test_get_users_success", method="GET"),
            _transaction(test_id="test_post_users_success", method="POST"),
        ),
        assertion_results=(
            _assertion(test_id="test_get_users_success", name="HTTP status", expected=200, actual=200),
            _assertion(test_id="test_post_users_success", name="HTTP status", expected=201, actual=201),
        ),
    )

    report = ReportEngine().generate_from_execution_summary(record)

    assert len(report.execution.tests) == 2
    by_id = {t.test_id: t for t in report.execution.tests}
    assert by_id["test_get_users_success"].transactions[0].method == "GET"
    assert by_id["test_get_users_success"].assertions[0].expected == 200
    assert by_id["test_post_users_success"].transactions[0].method == "POST"
    assert by_id["test_post_users_success"].assertions[0].expected == 201


def test_correlation_groups_a_test_id_that_only_has_assertions_no_transaction():
    # Ex.: asserção BROAD de status ("resposta presente") ainda registra um
    # AssertionResult mesmo quando, por algum motivo, a transação HTTP não
    # foi capturada — o test_id não pode desaparecer do relatório.
    record = _record(
        http_transactions=(),
        assertion_results=(_assertion(test_id="test_broad_only"),),
    )

    report = ReportEngine().generate_from_execution_summary(record)

    assert len(report.execution.tests) == 1
    assert report.execution.tests[0].test_id == "test_broad_only"
    assert report.execution.tests[0].transactions == ()


# --- 15/16. Resultado antigo sem http_transactions / sem assertion_results --


def test_old_result_without_http_transactions_still_renders():
    record = _record(http_transactions=(), assertion_results=())

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert report.execution.tests == ()
    assert "<h1>Execution Report</h1>" in html
    assert "PASSED" in html


def test_old_result_without_assertion_results_still_groups_by_transaction():
    # schema 1.4 (http_transactions existe, assertion_results ainda não).
    record = _record(http_transactions=(_transaction(),), assertion_results=())

    report = ReportEngine().generate_from_execution_summary(record)

    assert len(report.execution.tests) == 1
    assert report.execution.tests[0].assertions == ()
    assert report.execution.tests[0].transactions[0].method == "POST"


# --- 17. Newman continua funcionando ------------------------------------------


def test_newman_result_without_any_playwright_field_renders_exactly_as_before():
    # Newman nunca preenche http_transactions/assertion_results — o
    # ExecutionResultRecord default (()) já cobre isso; aqui só confirma
    # que a seção "Testes" nunca aparece e o resto do relatório permanece
    # intacto.
    record = _record()

    html = _render(record)

    assert "<h2>Testes</h2>" not in html
    assert "<h1>Execution Report</h1>" in html
    assert "Users API" in html


# --- P1.3 (Trace em falha): apresentação da referência já persistida -------


def test_report_engine_presents_the_trace_when_the_test_has_one():
    record = _record(
        assertion_results=(_assertion(status="FAILED", actual=500),),
        trace_artifacts=(_trace_artifact(),),
        success=False,
        failed_assertions=1,
    )

    report = ReportEngine().generate_from_execution_summary(record)

    test = report.execution.tests[0]
    assert test.trace is not None
    assert test.trace.test_id == "test_post_users_success"
    # Resolvido contra o diretório de result.json (source_path), nunca
    # relativo ao diretório de saída do próprio report.html.
    expected_dir = Path(record.source_path).resolve().parent
    assert test.trace.path == str((expected_dir / "traces/00-test_post_users_success.zip").resolve())


def test_report_engine_never_generates_a_trace_itself():
    # ReportEngine nunca gera trace — um teste PASSED sem trace_artifacts
    # nunca ganha um `test.trace` inventado.
    record = _record(assertion_results=(_assertion(status="PASSED"),), trace_artifacts=())

    report = ReportEngine().generate_from_execution_summary(record)

    assert report.execution.tests[0].trace is None


def test_html_shows_the_trace_link_when_available():
    record = _record(
        assertion_results=(_assertion(status="FAILED", actual=500),),
        trace_artifacts=(_trace_artifact(),),
        success=False,
        failed_assertions=1,
    )

    html = _render(record)

    assert "Ver Trace" in html
    assert "Trace dispon" in html  # "Trace disponível" (acento pode variar por escaping)


def test_html_never_shows_a_trace_link_for_a_passing_test():
    record = _record(assertion_results=(_assertion(status="PASSED"),), trace_artifacts=())

    html = _render(record)

    assert "Ver Trace" not in html


def test_old_result_without_trace_artifacts_still_renders():
    # schema 1.5 (trace_artifacts ainda não existe) — compatibilidade.
    record = _record(
        assertion_results=(_assertion(status="PASSED"),),
        trace_artifacts=(),
        schema_version="1.5",
    )

    html = _render(record)

    assert "Ver Trace" not in html
    assert "<h1>Execution Report</h1>" in html


def test_trace_link_is_a_clickable_file_uri():
    record = _record(
        assertion_results=(_assertion(status="FAILED", actual=500),),
        trace_artifacts=(_trace_artifact(),),
        success=False,
        failed_assertions=1,
    )

    html = _render(record)

    assert 'href="file://' in html


# --- Masking: dados já chegam mascarados, nunca reprocessados aqui ----------


def test_report_engine_never_alters_an_already_masked_value():
    record = _record(
        http_transactions=(
            _transaction(
                request_headers=(HttpTransactionHeader(name="Authorization", value="Bearer sk_l****3456"),),
            ),
        ),
        assertion_results=(_assertion(actual="sk_l****3456"),),
    )

    html = _render(record)

    assert "sk_l****3456" in html
    assert "sk_live" not in html
