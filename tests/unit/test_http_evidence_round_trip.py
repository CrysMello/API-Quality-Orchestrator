"""P2.1 — Captura e persistência de Request/Response HTTP por transação:
teste de integração PERMANENTE que percorre o pipeline real ponta a ponta

    HttpTransaction -> ExecutionResult -> PersistExecutionResultUseCase
    -> result.json -> JsonExecutionResultReader -> ReportEngine -> HTML

comparando os dados de evidência HTTP (method/URL/headers/query
parameters/body/status) antes e depois de cada etapa — nenhum campo pode
desaparecer, nenhuma transação pode vazar para o teste errado.

Única fronteira externa mockada: o repositório de persistência (grava em
tmp_path real — PersistExecutionResultUseCase._move_trace_artifacts faz
I/O real resolvido a partir do path devolvido pelo repositório, mesmo
padrão já usado em test_persist_execution_result_use_case.py).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from api_quality_agent.adapters.filesystem import JsonExecutionResultReader
from api_quality_agent.application.use_cases import PersistExecutionResultUseCase
from api_quality_agent.domain.models import (
    AssertionResult,
    ExecutionResult,
    ExecutionResultLocation,
    HttpTransaction,
    HttpTransactionHeader,
    InfrastructureFailure,
    InfrastructureFailureType,
)
from api_quality_agent.reporting import ReportEngine, render_execution_report_html

_STARTED_AT = datetime(2026, 8, 27, 9, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 27, 9, 0, 5, tzinfo=timezone.utc)
_SECRET_RAW = "sk_live_http_evidence_round_trip_secret"
_SECRET_MASKED = "sk_l****trip"


class _RealFileRepository:
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def save(self, *, content: str) -> ExecutionResultLocation:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / "result.json"
        path.write_text(content, encoding="utf-8")
        return ExecutionResultLocation(path=str(path))


def _run_pipeline(result: ExecutionResult, run_dir: Path):
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))
    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="Cotacao API",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
        workspace_id="ws-1",
        workspace_name="QA",
    )
    result_json_path = Path(location.path)
    raw_payload = json.loads(result_json_path.read_text(encoding="utf-8"))

    reader = JsonExecutionResultReader(run_dir.parent)
    record = reader.read(path=result_json_path)

    report = ReportEngine().generate_from_execution_summary(record)
    html = render_execution_report_html(
        report, source_path=record.source_path, schema_version=record.schema_version
    )
    return raw_payload, record, report, html


def _block_for(html: str, test_id: str) -> str:
    blocks = html.split('<div class="test-block">')[1:]
    return next(b for b in blocks if f"<h3>{test_id}</h3>" in b)


def test_full_request_response_round_trip_preserves_every_field(tmp_path):
    # Caso 1: request + response completos, com headers, query parameters
    # e body dos dois lados.
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=False,
        exit_code=1,
        duration_seconds=2.0,
        total_requests=1,
        total_assertions=1,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        http_transactions=(
            HttpTransaction(
                test_id="test_cotacao",
                method="POST",
                url="https://api.exemplo.com/cotacao?moeda=BRL",
                request_headers=(
                    HttpTransactionHeader(name="Content-Type", value="application/json"),
                    HttpTransactionHeader(name="Accept", value="application/json"),
                ),
                request_body='{"valor": 100, "plano": "basico"}',
                query_parameters=(HttpTransactionHeader(name="moeda", value="BRL"),),
                response_status=201,
                response_headers=(
                    HttpTransactionHeader(name="content-type", value="application/json"),
                ),
                response_body='{"id": "cot-1", "status": "aprovada"}',
            ),
        ),
        assertion_results=(
            AssertionResult(
                test_id="test_cotacao",
                name="HTTP status",
                expected=201,
                actual=201,
                status="PASSED",
                precision="EXACT",
                reason="Contrato define 201 para criação de cotação.",
            ),
        ),
    )

    raw_payload, record, report, html = _run_pipeline(result, tmp_path / "run_full")

    # --- Persistência: nenhum campo desaparece -------------------------------
    persisted = raw_payload["http_transactions"][0]
    assert persisted["method"] == "POST"
    assert persisted["url"] == "https://api.exemplo.com/cotacao?moeda=BRL"
    assert persisted["request_headers"] == {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    assert persisted["request_body"] == '{"valor": 100, "plano": "basico"}'
    assert persisted["query_parameters"] == {"moeda": "BRL"}
    assert persisted["response_status"] == 201
    assert persisted["response_headers"] == {"content-type": "application/json"}
    assert persisted["response_body"] == '{"id": "cot-1", "status": "aprovada"}'

    # --- Leitura: mesmos dados, agora como HttpTransaction real --------------
    read_transaction = record.http_transactions[0]
    assert read_transaction.method == persisted["method"]
    assert read_transaction.url == persisted["url"]
    assert {h.name: h.value for h in read_transaction.request_headers} == persisted[
        "request_headers"
    ]
    assert read_transaction.request_body == persisted["request_body"]
    assert {p.name: p.value for p in read_transaction.query_parameters} == persisted[
        "query_parameters"
    ]
    assert read_transaction.response_status == persisted["response_status"]
    assert {h.name: h.value for h in read_transaction.response_headers} == persisted[
        "response_headers"
    ]
    assert read_transaction.response_body == persisted["response_body"]

    # --- Reporting: ReportEngine reflete os mesmos dados lidos ---------------
    report_transaction = report.execution.tests[0].transactions[0]
    assert report_transaction.method == read_transaction.method
    assert report_transaction.url == read_transaction.url
    assert {h.name: h.value for h in report_transaction.request_headers} == {
        h.name: h.value for h in read_transaction.request_headers
    }
    assert {p.name: p.value for p in report_transaction.query_parameters} == {
        p.name: p.value for p in read_transaction.query_parameters
    }
    assert report_transaction.response_status == read_transaction.response_status
    assert report_transaction.response_body == read_transaction.response_body

    # --- HTML: tudo aparece, associado ao teste correto ----------------------
    block = _block_for(html, "test_cotacao")
    assert "POST" in block
    assert "api.exemplo.com/cotacao?moeda=BRL" in block
    assert "Query Parameters" in block and "BRL" in block
    assert "basico" in block  # request body
    assert "aprovada" in block  # response body
    assert "201" in block
    # Assertion continua independente da evidência (nunca misturada).
    assert "Expected" in block and "Actual" in block
    assert 'class="status status-passed"' in block


def test_request_without_body_and_response_without_body(tmp_path):
    # Casos 2 e 3.
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=True,
        exit_code=0,
        duration_seconds=1.0,
        total_requests=1,
        total_assertions=1,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        http_transactions=(
            HttpTransaction(
                test_id="test_delete",
                method="DELETE",
                url="https://api.exemplo.com/cotacao/1",
                request_headers=(),
                request_body=None,
                response_status=204,
                response_headers=(),
                response_body=None,
            ),
        ),
        assertion_results=(
            AssertionResult(
                test_id="test_delete",
                name="HTTP status",
                expected=204,
                actual=204,
                status="PASSED",
                precision="EXACT",
                reason="Contrato define 204 para exclusão.",
            ),
        ),
    )

    raw_payload, record, report, html = _run_pipeline(result, tmp_path / "run_no_body")

    assert raw_payload["http_transactions"][0]["request_body"] is None
    assert raw_payload["http_transactions"][0]["response_body"] is None
    assert record.http_transactions[0].request_body is None
    assert record.http_transactions[0].response_body is None

    block = _block_for(html, "test_delete")
    assert "No request body" in block
    assert "No response body" in block
    assert "Query Parameters" not in block  # caso 6: sem query parameters


def test_empty_headers_are_preserved_as_empty(tmp_path):
    # Caso 6: headers vazios (nenhum header registrado) — nunca inventa
    # header nenhum, mensagem explícita no HTML.
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=True,
        exit_code=0,
        duration_seconds=1.0,
        total_requests=1,
        total_assertions=0,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        http_transactions=(
            HttpTransaction(
                test_id="test_no_headers",
                method="GET",
                url="https://api.exemplo.com/health",
                request_headers=(),
                request_body=None,
                response_status=200,
                response_headers=(),
                response_body=None,
            ),
        ),
    )

    raw_payload, record, report, html = _run_pipeline(result, tmp_path / "run_no_headers")

    assert raw_payload["http_transactions"][0]["request_headers"] == {}
    assert raw_payload["http_transactions"][0]["response_headers"] == {}
    assert record.http_transactions[0].request_headers == ()
    assert record.http_transactions[0].response_headers == ()

    block = _block_for(html, "test_no_headers")
    assert "Nenhum header registrado" in block


def test_2xx_and_4xx_5xx_status_codes_round_trip_correctly(tmp_path):
    # Casos 9 e 10.
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=False,
        exit_code=1,
        duration_seconds=1.0,
        total_requests=2,
        total_assertions=2,
        failed_assertions=1,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        http_transactions=(
            HttpTransaction(
                test_id="test_ok",
                method="GET",
                url="https://api.exemplo.com/ok",
                request_headers=(),
                request_body=None,
                response_status=200,
                response_headers=(),
                response_body=None,
            ),
            HttpTransaction(
                test_id="test_not_found",
                method="GET",
                url="https://api.exemplo.com/missing",
                request_headers=(),
                request_body=None,
                response_status=404,
                response_headers=(),
                response_body=None,
            ),
        ),
    )

    raw_payload, record, report, html = _run_pipeline(result, tmp_path / "run_status")

    statuses = {t["test_id"]: t["response_status"] for t in raw_payload["http_transactions"]}
    assert statuses == {"test_ok": 200, "test_not_found": 404}
    tests_by_id = {t.test_id: t for t in report.execution.tests}
    assert tests_by_id["test_ok"].transactions[0].response_status == 200
    assert tests_by_id["test_not_found"].transactions[0].response_status == 404


def test_transaction_survives_alongside_evidence_failure_without_being_confused_with_it(
    tmp_path,
):
    # Caso 13: teste com evidence_failure — Request/Response e evidence_
    # failure continuam INDEPENDENTES (nunca um dentro do outro, nunca a
    # evidência de infraestrutura reinterpretada como transação HTTP).
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=True,
        exit_code=0,
        duration_seconds=1.0,
        total_requests=1,
        total_assertions=1,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        http_transactions=(
            HttpTransaction(
                test_id="test_with_evidence_failure",
                method="GET",
                url="https://api.exemplo.com/x",
                request_headers=(),
                request_body=None,
                response_status=200,
                response_headers=(),
                response_body='{"ok": true}',
            ),
        ),
        assertion_results=(
            AssertionResult(
                test_id="test_with_evidence_failure",
                name="HTTP status",
                expected=200,
                actual=200,
                status="PASSED",
                precision="EXACT",
                reason="Contrato define 200.",
            ),
        ),
        evidence_failures=(
            InfrastructureFailure(
                failure_type=InfrastructureFailureType.EVIDENCE_PERSISTENCE_FAILED,
                message="Falha ao mover o Trace para o destino final.",
                source="playwright_trace",
                test_id="test_with_evidence_failure",
            ),
        ),
    )

    _, _, report, html = _run_pipeline(result, tmp_path / "run_evidence")

    test = report.execution.tests[0]
    assert len(test.transactions) == 1
    assert len(test.evidence_failures) == 1
    block = _block_for(html, "test_with_evidence_failure")
    assert 'class="status status-passed"' in block
    assert "Infraestrutura de evidências" in block
    assert "POST" not in block and "GET" in block  # request presente, sem confusão


def test_test_without_http_transaction_never_fabricates_one(tmp_path):
    # Caso 14: teste sem nenhuma HttpTransaction — nunca inventa uma.
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=True,
        exit_code=0,
        duration_seconds=1.0,
        total_requests=0,
        total_assertions=1,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        assertion_results=(
            AssertionResult(
                test_id="test_sem_transacao",
                name="Regra de negócio",
                expected=True,
                actual=True,
                status="PASSED",
                precision="EXACT",
                reason="Validação que não depende de uma chamada HTTP.",
            ),
        ),
    )

    _, _, report, html = _run_pipeline(result, tmp_path / "run_no_transaction")

    test = report.execution.tests[0]
    assert test.transactions == ()
    block = _block_for(html, "test_sem_transacao")
    assert "Nenhuma transação HTTP registrada" in block


def test_multiple_tests_with_different_transactions_never_cross_contaminate(tmp_path):
    # Caso 15 + correlação obrigatória test_A/test_B (mesmo endpoint,
    # métodos e status diferentes).
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=False,
        exit_code=1,
        duration_seconds=2.0,
        total_requests=2,
        total_assertions=2,
        failed_assertions=1,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        http_transactions=(
            HttpTransaction(
                test_id="test_A",
                method="POST",
                url="https://api.exemplo.com/cotacao",
                request_headers=(HttpTransactionHeader(name="X-Plano", value="basico"),),
                request_body='{"cliente": "solicitante-A"}',
                response_status=201,
                response_headers=(),
                response_body='{"id": "cot-A"}',
            ),
            HttpTransaction(
                test_id="test_B",
                method="POST",
                url="https://api.exemplo.com/cotacao",
                request_headers=(HttpTransactionHeader(name="X-Plano", value="premium"),),
                request_body='{"cliente": "solicitante-B"}',
                response_status=500,
                response_headers=(),
                response_body='{"error": "falha ao processar"}',
            ),
        ),
        assertion_results=(
            AssertionResult(
                test_id="test_A",
                name="HTTP status",
                expected=201,
                actual=201,
                status="PASSED",
                precision="EXACT",
                reason="Contrato define 201.",
            ),
            AssertionResult(
                test_id="test_B",
                name="HTTP status",
                expected=201,
                actual=500,
                status="FAILED",
                precision="EXACT",
                reason="Contrato define 201.",
            ),
        ),
    )

    raw_payload, record, report, html = _run_pipeline(result, tmp_path / "run_multi")

    tests_by_id = {t.test_id: t for t in report.execution.tests}
    a = tests_by_id["test_A"].transactions[0]
    b = tests_by_id["test_B"].transactions[0]
    assert a.response_status == 201 and b.response_status == 500
    assert a.response_body == '{"id": "cot-A"}'
    assert b.response_body == '{"error": "falha ao processar"}'
    assert {h.name: h.value for h in a.request_headers} == {"X-Plano": "basico"}
    assert {h.name: h.value for h in b.request_headers} == {"X-Plano": "premium"}

    block_a = _block_for(html, "test_A")
    block_b = _block_for(html, "test_B")
    assert "solicitante-A" in block_a and "solicitante-A" not in block_b
    assert "solicitante-B" in block_b and "solicitante-B" not in block_a
    assert "cot-A" in block_a and "cot-A" not in block_b
    assert 'class="status status-passed"' in block_a
    assert 'class="status status-failed"' in block_b


def test_known_secret_in_header_and_body_is_masked_through_the_whole_pipeline(tmp_path):
    # Caso 16: valor já mascarado (mesma convenção do resto do projeto —
    # esta camada nunca re-mascara, PlaywrightAdapter já entrega o dado
    # pronto) sobrevive intacto de ponta a ponta; caso 17: conteúdo não
    # secreto ao lado permanece preservado.
    result = ExecutionResult(
        collection_source="/tmp/suite",
        success=True,
        exit_code=0,
        duration_seconds=1.0,
        total_requests=1,
        total_assertions=0,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=None,
        stdout="",
        stderr="",
        http_transactions=(
            HttpTransaction(
                test_id="test_secret",
                method="POST",
                url="https://api.exemplo.com/login",
                request_headers=(
                    HttpTransactionHeader(name="Authorization", value=f"Bearer {_SECRET_MASKED}"),
                    HttpTransactionHeader(name="X-Request-Id", value="req-nao-secreto-123"),
                ),
                request_body=f'{{"token": "{_SECRET_MASKED}", "device": "nao-secreto"}}',
                query_parameters=(
                    HttpTransactionHeader(name="api_key", value=_SECRET_MASKED),
                ),
                response_status=200,
                response_headers=(),
                response_body=None,
            ),
        ),
    )

    raw_payload, record, report, html = _run_pipeline(result, tmp_path / "run_secret")

    dumped = json.dumps(raw_payload)
    assert _SECRET_RAW not in dumped
    assert _SECRET_RAW not in html
    assert _SECRET_MASKED in dumped
    assert _SECRET_MASKED in html
    # Conteúdo não secreto ao lado do secreto continua visível/auditável.
    assert "req-nao-secreto-123" in html
    assert "nao-secreto" in html
