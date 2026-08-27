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
    InfrastructureFailure,
    InfrastructureFailureType,
    TestFailure,
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


def _evidence_failure(**overrides) -> InfrastructureFailure:
    defaults = dict(
        failure_type=InfrastructureFailureType.EVIDENCE_PERSISTENCE_FAILED,
        message="Falha ao mascarar o Trace; artefato não persistido por segurança.",
        source="playwright_trace",
        test_id="test_post_orders_fail",
    )
    defaults.update(overrides)
    return InfrastructureFailure(**defaults)


def _test_failure(**overrides) -> TestFailure:
    defaults = dict(
        request_name=None,
        test_name="test_post_orders_success",
        error_message="playwright._impl._errors.Error: connect ECONNREFUSED",
    )
    defaults.update(overrides)
    return TestFailure(**defaults)


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


def test_correlation_groups_a_test_id_that_only_has_a_trace_no_transaction_or_assertion():
    # P1.4 (hardening): um teste que falha por erro de TRANSPORTE (timeout/
    # connection error — nunca chega a existir uma resposta) não registra
    # HttpTransaction nem AssertionResult, só o trace — sem este caso, o
    # test_id (e o trace) desapareceriam completamente do relatório.
    record = _record(
        http_transactions=(),
        assertion_results=(),
        trace_artifacts=(_trace_artifact(test_id="test_transport_error_only"),),
        success=False,
        failed_assertions=1,
    )

    report = ReportEngine().generate_from_execution_summary(record)

    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert test.test_id == "test_transport_error_only"
    assert test.transactions == ()
    assert test.assertions == ()
    assert test.trace is not None
    assert test.trace.test_id == "test_transport_error_only"


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


def _write_real_result_with_trace_file(tmp_path):
    # P1.4 (hardening): "available" agora depende do arquivo existir de
    # verdade no disco — helper compartilhado que cria um result.json real
    # num diretório real, com o .zip referenciado realmente presente ao
    # lado (traces/...), pra exercitar o caminho feliz.
    run_dir = tmp_path / "run_x"
    run_dir.mkdir()
    traces_dir = run_dir / "traces"
    traces_dir.mkdir()
    (traces_dir / "00-test_post_users_success.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    source_path = str(run_dir / "result.json")
    record = _record(
        source_path=source_path,
        assertion_results=(_assertion(status="FAILED", actual=500),),
        trace_artifacts=(_trace_artifact(),),
        success=False,
        failed_assertions=1,
    )
    return record


def test_html_shows_the_trace_link_when_available(tmp_path):
    record = _write_real_result_with_trace_file(tmp_path)

    html = _render(record)

    assert "Ver Trace" in html
    assert "Trace dispon" in html  # "Trace disponível" (acento pode variar por escaping)
    assert "indispon" not in html


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


def test_trace_link_is_a_clickable_file_uri(tmp_path):
    record = _write_real_result_with_trace_file(tmp_path)

    html = _render(record)

    assert 'href="file://' in html


def test_html_shows_trace_unavailable_when_the_file_no_longer_exists():
    # P1.4 (hardening) — item 8: result.json aponta pra um arquivo que não
    # existe (movido/apagado depois). Nunca um link morto silencioso, nunca
    # apaga a referência, nunca quebra o restante do relatório.
    record = _record(
        assertion_results=(_assertion(status="FAILED", actual=500),),
        trace_artifacts=(_trace_artifact(),),  # aponta pra um caminho que nunca existiu
        success=False,
        failed_assertions=1,
    )

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert report.execution.tests[0].trace is not None
    assert report.execution.tests[0].trace.available is False
    assert "Ver Trace" not in html
    assert "indispon" in html
    # o restante do relatório continua presente.
    assert "<h1>Execution Report</h1>" in html
    assert "test_post_users_success" in html


# --- P1.5 (infrastructure failure das evidências) --------------------------


def test_report_engine_groups_evidence_failure_under_its_test_id():
    record = _record(
        assertion_results=(_assertion(test_id="test_post_orders_fail", status="FAILED", actual=500),),
        evidence_failures=(_evidence_failure(),),
        success=False,
        failed_assertions=1,
    )

    report = ReportEngine().generate_from_execution_summary(record)

    test = report.execution.tests[0]
    assert len(test.evidence_failures) == 1
    failure = test.evidence_failures[0]
    assert failure.test_id == "test_post_orders_fail"
    assert failure.source == "playwright_trace"
    assert "mascarar" in failure.message.lower()
    # A falha de evidência nunca altera o status FUNCIONAL do teste
    # (decidido só pelas assertions).
    assert test.assertions[0].status == "FAILED"


def test_report_engine_never_generates_an_evidence_failure_itself():
    # Item 18: teste PASS sem nenhum evidence_failures real -> nunca
    # inventado.
    record = _record(assertion_results=(_assertion(status="PASSED"),), evidence_failures=())

    report = ReportEngine().generate_from_execution_summary(record)

    assert report.execution.tests[0].evidence_failures == ()


def test_correlation_groups_a_test_id_that_only_has_an_evidence_failure():
    # Uma falha de evidência sem HttpTransaction/AssertionResult/
    # TraceArtifact (ex.: mkdir falhou antes de qualquer trace existir)
    # nunca desaparece do relatório.
    record = _record(
        http_transactions=(),
        assertion_results=(),
        evidence_failures=(_evidence_failure(test_id="test_transport_error_only"),),
        success=False,
        failed_assertions=1,
    )

    report = ReportEngine().generate_from_execution_summary(record)

    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert test.test_id == "test_transport_error_only"
    assert test.transactions == ()
    assert test.assertions == ()
    assert len(test.evidence_failures) == 1


def test_evidence_failure_without_test_id_is_never_associated_with_any_test():
    # Item 10: uma InfrastructureFailure sem test_id (falha ocorrida antes
    # de existir um) nunca é inventada nem associada a um teste qualquer.
    record = _record(
        assertion_results=(_assertion(status="PASSED"),),
        evidence_failures=(_evidence_failure(test_id=None),),
    )

    report = ReportEngine().generate_from_execution_summary(record)

    assert len(report.execution.tests) == 1
    assert report.execution.tests[0].evidence_failures == ()


def test_html_shows_the_infrastructure_evidence_failure_block():
    record = _record(
        assertion_results=(_assertion(test_id="test_post_orders_fail", status="FAILED", actual=500),),
        evidence_failures=(_evidence_failure(),),
        success=False,
        failed_assertions=1,
    )

    html = _render(record)

    assert "Infraestrutura de evidências" in html
    assert "Falha ao mascarar o Trace" in html


def test_html_never_shows_the_evidence_failure_block_when_there_is_none():
    record = _record(assertion_results=(_assertion(status="PASSED"),), evidence_failures=())

    html = _render(record)

    assert "Infraestrutura de evidências" not in html


def test_html_never_presents_an_evidence_failure_as_an_assertion():
    # O bloco de evidence_failures nunca deve usar as classes CSS de
    # assertion (assertion/assertion-passed/assertion-failed) — é
    # visualmente e semanticamente distinto.
    record = _record(
        assertion_results=(_assertion(test_id="test_post_orders_fail", status="FAILED", actual=500),),
        evidence_failures=(_evidence_failure(),),
        success=False,
        failed_assertions=1,
    )

    html = _render(record)

    evidence_start = html.index("Infraestrutura de evidências")
    evidence_block_start = html.rindex("<div", 0, evidence_start)
    evidence_block_end = html.index("</div>", evidence_start)
    evidence_block = html[evidence_block_start:evidence_block_end]
    assert "assertion-passed" not in evidence_block
    assert "assertion-failed" not in evidence_block


def test_evidence_failure_message_secrets_are_preserved_as_already_masked():
    # ReportEngine nunca reprocessa masking — a mensagem já chega pronta
    # do result.json (mesmo contrato do resto da evidência).
    record = _record(
        assertion_results=(_assertion(test_id="test_post_orders_fail", status="FAILED", actual=500),),
        evidence_failures=(
            _evidence_failure(message="Falha ao mover artefato. Token: sk_l****3456"),
        ),
        success=False,
        failed_assertions=1,
    )

    html = _render(record)

    assert "sk_l****3456" in html
    assert "sk_live" not in html


def test_old_result_without_evidence_failures_still_renders():
    # schema 1.6 (evidence_failures ainda não existe) — compatibilidade.
    record = _record(
        assertion_results=(_assertion(status="PASSED"),),
        evidence_failures=(),
        schema_version="1.6",
    )

    html = _render(record)

    assert "Infraestrutura de evidências" not in html
    assert "<h1>Execution Report</h1>" in html


def test_newman_result_never_shows_any_evidence_failure_block():
    # Newman nunca preenche evidence_failures — o default (()) já cobre
    # isso; aqui só confirma que o bloco nunca aparece.
    record = _record()

    html = _render(record)

    assert "Infraestrutura de evidências" not in html


# --- P1.6 (status visual por teste): test_failures sem assertions --------


def test_html_shows_failed_when_there_is_a_test_failure_and_no_assertions():
    # Caso relatado: erro de transporte (TestFailure) sem nenhuma
    # HttpTransaction/AssertionResult para o mesmo test_id — o selo do
    # teste precisa ser FAILED, nunca N/A.
    record = _record(
        assertion_results=(),
        test_failures=(_test_failure(test_name="test_post_orders_success"),),
        trace_artifacts=(_trace_artifact(test_id="test_post_orders_success"),),
        success=False,
        failed_assertions=0,
    )

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert len(report.execution.tests) == 1
    assert report.execution.tests[0].test_id == "test_post_orders_success"
    assert report.execution.tests[0].assertions == ()
    assert 'class="status status-failed"' in html
    assert "N/A" not in html


def test_html_shows_failed_when_there_is_a_test_failure_and_assertions():
    # TestFailure sempre vence, mesmo quando existem assertions (ex.:
    # PASSED registrado antes de um erro de teardown derrubar o teste).
    record = _record(
        assertion_results=(_assertion(status="PASSED"),),
        test_failures=(_test_failure(test_name="test_post_users_success"),),
        success=False,
    )

    html = _render(record)

    assert 'class="status status-failed"' in html


def test_html_shows_failed_when_no_test_failure_but_an_assertion_failed():
    record = _record(
        assertion_results=(_assertion(status="FAILED", actual=500),),
        test_failures=(),
        success=False,
        failed_assertions=1,
    )

    html = _render(record)

    assert 'class="status status-failed"' in html


def test_html_shows_passed_when_no_test_failure_and_all_assertions_passed():
    record = _record(
        assertion_results=(_assertion(status="PASSED"),),
        test_failures=(),
    )

    html = _render(record)

    assert 'class="status status-passed"' in html


def test_html_shows_na_when_no_test_failure_and_no_assertions():
    # Item 4 da regra: sem informação suficiente (nem TestFailure, nem
    # assertion) — N/A, nunca inventado.
    record = _record(
        http_transactions=(),
        assertion_results=(),
        trace_artifacts=(_trace_artifact(test_id="test_transport_error_only"),),
        test_failures=(),
    )

    html = _render(record)

    assert 'class="status">N/A</span>' in html


def test_evidence_failure_never_turns_a_passing_test_into_failed():
    # Regra explícita: evidence_failures nunca decide o selo do teste.
    record = _record(
        assertion_results=(_assertion(status="PASSED"),),
        evidence_failures=(_evidence_failure(test_id="test_post_users_success"),),
        test_failures=(),
    )

    html = _render(record)

    assert 'class="status status-passed"' in html
    assert 'class="status status-failed"' not in html


def test_evidence_failure_alone_is_never_treated_as_a_test_failure():
    # Um evidence_failure isolado (sem TestFailure, sem assertions) nunca
    # produz um selo FAILED — continua N/A (informação insuficiente sobre
    # o resultado FUNCIONAL do teste).
    record = _record(
        http_transactions=(),
        assertion_results=(),
        evidence_failures=(_evidence_failure(test_id="test_transport_error_only"),),
        test_failures=(),
    )

    html = _render(record)

    assert 'class="status">N/A</span>' in html
    assert 'class="status status-failed"' not in html


# --- P1.6 (achado do sanity check): test_failure SEM NENHUMA evidência ------
# --- correlacionada (nem HttpTransaction, nem AssertionResult, nem Trace, --
# --- nem evidence_failure) também precisa entrar em                      --
# --- report.execution.tests — antes, esse teste desaparecia inteiramente --
# --- da visão detalhada por teste, só sobrando na seção "Falhas" legada. --


def test_a_isolated_test_failure_without_any_evidence_appears_as_failed():
    # Caso extremo do achado: erro de transporte puro (ex.: connect
    # ECONNREFUSED antes de qualquer request) — nenhuma HttpTransaction,
    # nenhuma AssertionResult, nenhum TraceArtifact, nenhum evidence_failure
    # para este test_id. Antes desta correção, o teste nem aparecia em
    # report.execution.tests.
    record = _record(
        http_transactions=(),
        assertion_results=(),
        test_failures=(_test_failure(test_name="test_connection_refused"),),
        success=False,
        total_requests=0,
        total_assertions=0,
    )

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert test.test_id == "test_connection_refused"
    # Nenhum dado artificial é inventado: nem assertion, nem transação, nem
    # trace, nem evidence_failure — só o test_id passa a existir no grupo.
    assert test.transactions == ()
    assert test.assertions == ()
    assert test.trace is None
    assert test.evidence_failures == ()
    assert 'class="status status-failed"' in html
    assert "N/A" not in html
    assert "Ver Trace" not in html


def test_b_test_failure_with_http_transaction_keeps_existing_behavior():
    # test_failure + HttpTransaction (sem assertion) — já funcionava antes
    # (a transação já colocava o test_id em `order`); esta correção não
    # pode alterar esse comportamento.
    record = _record(
        http_transactions=(_transaction(test_id="test_post_orders_success"),),
        assertion_results=(),
        test_failures=(_test_failure(test_name="test_post_orders_success"),),
        success=False,
    )

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert len(test.transactions) == 1
    assert test.assertions == ()
    assert 'class="status status-failed"' in html


def test_c_test_failure_with_assertion_keeps_existing_behavior():
    # test_failure + AssertionResult (PASSED, ex.: falha no teardown depois
    # do assert já ter passado) — comportamento existente preservado.
    record = _record(
        assertion_results=(_assertion(status="PASSED"),),
        test_failures=(_test_failure(test_name="test_post_users_success"),),
        success=False,
    )

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert len(test.assertions) == 1
    assert test.assertions[0].status == "PASSED"
    assert 'class="status status-failed"' in html


def test_d_test_failure_and_evidence_failure_both_appear_on_the_same_test():
    record = _record(
        http_transactions=(),
        assertion_results=(),
        test_failures=(_test_failure(test_name="test_checkout_flow"),),
        evidence_failures=(_evidence_failure(test_id="test_checkout_flow"),),
        success=False,
    )

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert test.test_id == "test_checkout_flow"
    assert len(test.evidence_failures) == 1
    assert 'class="status status-failed"' in html
    assert "Infraestrutura de evidências" in html


def test_e_multiple_isolated_test_failures_each_get_their_own_block():
    record = _record(
        http_transactions=(),
        assertion_results=(),
        test_failures=(
            _test_failure(test_name="test_one"),
            _test_failure(test_name="test_two"),
            _test_failure(test_name="test_three"),
        ),
        success=False,
    )

    report = ReportEngine().generate_from_execution_summary(record)

    assert [test.test_id for test in report.execution.tests] == [
        "test_one",
        "test_two",
        "test_three",
    ]
    for test in report.execution.tests:
        assert test.transactions == ()
        assert test.assertions == ()
        assert test.evidence_failures == ()


def test_f_isolated_test_failure_of_one_test_never_leaks_into_another():
    record = _record(
        http_transactions=(_transaction(test_id="test_B"),),
        assertion_results=(_assertion(test_id="test_B", status="PASSED"),),
        test_failures=(_test_failure(test_name="test_A"),),
        success=False,
    )

    report = ReportEngine().generate_from_execution_summary(record)
    tests_by_id = {test.test_id: test for test in report.execution.tests}

    assert set(tests_by_id.keys()) == {"test_A", "test_B"}
    assert tests_by_id["test_A"].transactions == ()
    assert tests_by_id["test_A"].assertions == ()
    assert tests_by_id["test_B"].assertions[0].status == "PASSED"

    html = _render(record)
    blocks = html.split('<div class="test-block">')[1:]
    block_a = next(b for b in blocks if "<h3>test_A</h3>" in b)
    block_b = next(b for b in blocks if "<h3>test_B</h3>" in b)
    assert 'class="status status-failed"' in block_a
    assert 'class="status status-passed"' in block_b
    assert "test_A" not in block_b


def test_g_normal_pass_is_not_affected_by_the_test_failure_correlation():
    record = _record(
        assertion_results=(_assertion(status="PASSED"),),
        test_failures=(),
    )

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert len(report.execution.tests) == 1
    assert 'class="status status-passed"' in html
    assert 'class="status status-failed"' not in html


def test_h_isolated_evidence_failure_still_does_not_become_a_test_failure():
    # Mesmo depois de test_failures virar uma fonte de correlação, um
    # evidence_failure isolado (sem TestFailure nenhum) continua N/A — a
    # regra de que evidence_failure nunca decide o selo continua intacta.
    record = _record(
        http_transactions=(),
        assertion_results=(),
        evidence_failures=(_evidence_failure(test_id="test_transport_error_only"),),
        test_failures=(),
    )

    report = ReportEngine().generate_from_execution_summary(record)
    html = _render(record)

    assert len(report.execution.tests) == 1
    assert 'class="status">N/A</span>' in html
    assert 'class="status status-failed"' not in html


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


# --- P2.1 (evidência HTTP): query parameters --------------------------------


def test_report_engine_groups_query_parameters_under_its_transaction():
    record = _record(
        http_transactions=(
            _transaction(
                query_parameters=(
                    HttpTransactionHeader(name="page", value="2"),
                    HttpTransactionHeader(name="limit", value="10"),
                ),
            ),
        ),
    )

    report = ReportEngine().generate_from_execution_summary(record)

    transaction = report.execution.tests[0].transactions[0]
    assert {p.name: p.value for p in transaction.query_parameters} == {
        "page": "2",
        "limit": "10",
    }


def test_html_shows_query_parameters_section_when_present():
    record = _record(
        http_transactions=(
            _transaction(
                query_parameters=(HttpTransactionHeader(name="page", value="2"),),
            ),
        ),
    )

    html = _render(record)

    assert "Query Parameters" in html
    assert "page" in html


def test_html_omits_query_parameters_section_when_absent():
    # Query parameters são opcionais por natureza — a seção inteira some
    # (nunca "Nenhum query parameter registrado" poluindo toda transação
    # sem query string), diferente do comportamento de Request/Response
    # Headers (que sempre mostram a seção, mesmo vazia).
    record = _record(http_transactions=(_transaction(query_parameters=()),))

    html = _render(record)

    assert "Query Parameters" not in html


def test_old_result_without_query_parameters_still_renders():
    # Resultado anterior ao P2.1 (schema < 1.8): HttpTransaction construído
    # sem query_parameters (default ()) — nunca quebra a renderização.
    record = _record(http_transactions=(_transaction(),), schema_version="1.7")

    html = _render(record)

    assert "Query Parameters" not in html
    assert "<h1>Execution Report</h1>" in html


def test_query_parameters_never_alter_the_url_field():
    record = _record(
        http_transactions=(
            _transaction(
                url="https://api.exemplo.com/users?page=2",
                query_parameters=(HttpTransactionHeader(name="page", value="2"),),
            ),
        ),
    )

    report = ReportEngine().generate_from_execution_summary(record)

    transaction = report.execution.tests[0].transactions[0]
    assert transaction.url == "https://api.exemplo.com/users?page=2"


def test_query_parameters_of_one_test_never_leak_into_another():
    # Correlação obrigatória do P2.1: test_A (POST /cotacao, 201) e test_B
    # (POST /cotacao, 500) nunca compartilham request/response/query
    # parameters, mesmo com o mesmo endpoint/método.
    record = _record(
        http_transactions=(
            _transaction(
                test_id="test_A",
                url="https://api.exemplo.com/cotacao",
                response_status=201,
                query_parameters=(HttpTransactionHeader(name="plano", value="basico"),),
                request_body="cliente-somente-em-A",
            ),
            _transaction(
                test_id="test_B",
                url="https://api.exemplo.com/cotacao",
                response_status=500,
                query_parameters=(HttpTransactionHeader(name="plano", value="premium"),),
                request_body="cliente-somente-em-B",
            ),
        ),
        assertion_results=(
            _assertion(test_id="test_A", expected=201, actual=201, status="PASSED"),
            _assertion(test_id="test_B", expected=201, actual=500, status="FAILED"),
        ),
    )

    report = ReportEngine().generate_from_execution_summary(record)
    tests_by_id = {t.test_id: t for t in report.execution.tests}

    a_transaction = tests_by_id["test_A"].transactions[0]
    b_transaction = tests_by_id["test_B"].transactions[0]
    assert a_transaction.response_status == 201
    assert b_transaction.response_status == 500
    assert {p.name: p.value for p in a_transaction.query_parameters} == {"plano": "basico"}
    assert {p.name: p.value for p in b_transaction.query_parameters} == {"plano": "premium"}
    assert a_transaction.request_body != b_transaction.request_body

    html = _render(record)
    blocks = html.split('<div class="test-block">')[1:]
    block_a = next(b for b in blocks if "<h3>test_A</h3>" in b)
    block_b = next(b for b in blocks if "<h3>test_B</h3>" in b)
    assert "basico" in block_a and "premium" not in block_a
    assert "premium" in block_b and "basico" not in block_b
    assert "cliente-somente-em-A" in block_a and "cliente-somente-em-A" not in block_b
    assert "cliente-somente-em-B" in block_b and "cliente-somente-em-B" not in block_a


def test_known_secret_is_masked_in_query_parameters_html():
    record = _record(
        http_transactions=(
            _transaction(
                query_parameters=(HttpTransactionHeader(name="api_key", value="sk_l****3456"),),
            ),
        ),
    )

    html = _render(record)

    assert "sk_l****3456" in html
    assert "sk_live" not in html
