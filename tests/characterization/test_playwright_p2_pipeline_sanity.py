"""P2.3 — Sanity check E2E permanente do pipeline de evidências Playwright.

Valida, com execução REAL (Playwright real, pytest real, servidor HTTP
real em localhost — nunca fake_pytest.py, nunca um gerador mockado), a
cadeia completa:

    TestStrategy -> PlaywrightEndpointTestGenerator -> código gerado
    -> execução real -> AssertionResult / HTTP Evidence / Trace / failures
    -> ExecutionResult -> PersistExecutionResultUseCase -> result.json
    -> JsonExecutionResultReader -> ReportEngine -> ReportExecutionSection
    -> HTML Renderer

Pergunta principal: quando diferentes tipos de resultado/evidência
acontecem simultaneamente (PASS, FAIL, múltiplas assertions, trace,
evidence_failure, skipped), todas as informações permanecem corretas,
isoladas e auditáveis até o HTML?

FRONTEIRAS MOCKADAS (documentadas explicitamente, nunca o objeto sob
validação):
  - Repositório de persistência (`_RealFileRepository`): grava em
    tmp_path real — mesmo padrão já usado em
    tests/unit/test_http_evidence_round_trip.py. A lógica real de
    PersistExecutionResultUseCase roda sem mock.
  - `shutil.move` (só no teste do Cenário E): simula uma falha real de
    I/O (disco cheio, permissão negada) na hora de mover o trace já
    capturado — nunca mocka InfrastructureFailure/evidence_failures em
    si, só a chamada de sistema que o código de produção já trata como
    falível.

Nenhum outro mock. Nenhuma funcionalidade nova. Nenhum código de
produção alterado. Fluxo Postman/Newman inteiramente fora deste arquivo.

Documenta o comportamento ATUAL — se quebrar por uma mudança deliberada,
atualize-o conscientemente (ver tests/characterization/README.md).
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api_quality_agent.adapters.filesystem import JsonExecutionResultReader
from api_quality_agent.adapters.playwright import PlaywrightAdapter
from api_quality_agent.application.use_cases import PersistExecutionResultUseCase
from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionResult,
    AssertionType,
    ExecutionContext,
    ExecutionMode,
    ExecutionResult,
    ExecutionResultLocation,
    HttpTransaction,
    InfrastructureFailure,
    InfrastructureFailureType,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    DefaultPlaywrightTestSuiteBuilder,
    PlaywrightEndpointTestGenerator,
)
from api_quality_agent.parsers import PostmanCollectionParser
from api_quality_agent.reporting import ReportEngine, render_execution_report_html
from api_quality_agent.shared.masking import mask_secret
from postman_test_server import PostmanTestServer

_STARTED_AT = datetime(2026, 8, 28, 9, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 28, 9, 1, 0, tzinfo=timezone.utc)
_SECRET = "sk_live_p23_e2e_sanity_secret_998877"
_MASKED_SECRET = mask_secret(_SECRET)


class _RealFileRepository:
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def save(self, *, content: str) -> ExecutionResultLocation:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / "result.json"
        path.write_text(content, encoding="utf-8")
        return ExecutionResultLocation(path=str(path))


def _analyzed(request: dict):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "P2.3 Sanity",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "R", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _generated_endpoint(request: dict, assertions: tuple[AssertionDefinition, ...]):
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)


def _status_assertion(status_code: int) -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
        origin="contract",
    )


def _valid_json_body_assertion() -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.VALID_JSON_BODY,
        description="O corpo da resposta deve ser um JSON válido.",
        expected_value=None,
        origin="contract",
    )


def _schema_assertion(schema: dict) -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.SCHEMA,
        description="O corpo da resposta deve validar contra o schema esperado.",
        expected_value=schema,
        origin="contract",
    )


def _write_suite(tmp_path: Path, name: str, generated_endpoints) -> Path:
    execution_context = ExecutionContext.create(
        mode=ExecutionMode.OFFLINE,
        source="playwright-generation",
        workspace_id=None,
        collection_id="col-1",
        collection_name="P2.3 Sanity",
        id_factory=lambda: f"exec-{name}",
    )
    suite = DefaultPlaywrightTestSuiteBuilder().build(list(generated_endpoints), execution_context)
    suite_dir = tmp_path / f"suite_{name}"
    for generated_file in suite.files:
        file_path = suite_dir / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(generated_file.content, encoding="utf-8")
    return suite_dir


def _run_real(suite_dir: Path, base_url: str, monkeypatch, *, known_secret_values=()) -> ExecutionResult:
    monkeypatch.setenv("PLAYWRIGHT_BASE_URL", base_url)
    adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
    return adapter.run(
        tests_path=str(suite_dir), timeout_seconds=120.0, known_secret_values=known_secret_values
    )


def _persist_read_report_html(result: ExecutionResult, run_dir: Path):
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))
    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="P2.3 Sanity",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
        workspace_id=None,
        workspace_name=None,
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


def _assertion_by_name(assertions, name: str):
    return next(a for a in assertions if a.name == name)


# ============================================================================
# Suíte combinada real: test_A (PASS), test_B (1 assertion falha),
# test_C (múltiplas assertions falham + trace), test_secure (PASS +
# masking), skip endpoint (unresolved path variable -> placeholder).
# ============================================================================


@pytest.fixture(scope="module")
def combined_pipeline(tmp_path_factory, monkeypatch_module):
    tmp_path = tmp_path_factory.mktemp("p23_combined")
    server = PostmanTestServer()
    try:
        server.set_route("/users/1", method="GET", status=200, body={"id": 1, "name": "Ana"})
        server.set_route("/orders/1", method="GET", status=200, body={"id": 55, "status": "placed"})
        server.set_route(
            "/products/1", method="GET", status=200, body={"id": 123, "name": "Widget"}
        )
        server.set_route(
            f"/secure/echo?token={_SECRET}",
            method="PUT",
            status=200,
            body={"apiKey": _SECRET, "ok": True},
            extra_headers={"X-Session-Token": _SECRET},
        )

        # test_A — PASS completo: status + presença + tipo + json_schema.
        endpoint_a = _generated_endpoint(
            {"request": {"method": "GET", "url": "https://api.exemplo.com/users/1"}},
            (
                _status_assertion(200),
                _valid_json_body_assertion(),
                _schema_assertion(
                    {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    }
                ),
            ),
        )

        # test_B — UMA assertion falha (status), as demais continuam PASSED.
        endpoint_b = _generated_endpoint(
            {"request": {"method": "GET", "url": "https://api.exemplo.com/orders/1"}},
            (
                _status_assertion(201),  # real: 200 -> FAILED
                _valid_json_body_assertion(),
                _schema_assertion(
                    {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                        "required": ["id"],
                    }
                ),
            ),
        )

        # test_C — MÚLTIPLAS assertions falham simultaneamente (status +
        # json_schema/const + expected_value), gera trace real (falhou).
        endpoint_c = _generated_endpoint(
            {"request": {"method": "GET", "url": "https://api.exemplo.com/products/1"}},
            (
                _status_assertion(201),  # real: 200 -> FAILED
                _valid_json_body_assertion(),
                _schema_assertion(
                    {
                        "type": "object",
                        "properties": {"id": {"const": 999}, "name": {"type": "string"}},
                        "required": ["name"],
                    }
                ),  # real id: 123 -> json_schema E expected_value:id FAILED; required_field:name PASSED
            ),
        )

        # test_secure — PASS + evidência HTTP contendo um secret conhecido
        # em header/query/body de request e em body/header de response
        # (masking, Cenário J).
        endpoint_secure = _generated_endpoint(
            {
                "request": {
                    "method": "PUT",
                    "url": {
                        "raw": f"https://api.exemplo.com/secure/echo?token={_SECRET}",
                        "protocol": "https",
                        "host": ["api", "exemplo", "com"],
                        "path": ["secure", "echo"],
                        "query": [{"key": "token", "value": _SECRET}],
                    },
                    "header": [
                        {"key": "X-Api-Key", "value": _SECRET},
                        {"key": "Content-Type", "value": "application/json"},
                    ],
                    "body": {"mode": "raw", "raw": json.dumps({"apiKey": _SECRET})},
                }
            },
            (_status_assertion(200),),
        )

        # skip endpoint — path variable sem default na Collection -> cai no
        # PlaceholderEndpointTestGenerator real (nunca simulado), que marca
        # @pytest.mark.skip; nenhuma funcionalidade nova, mesmo mecanismo já
        # existente para endpoint não suportado.
        endpoint_skip = _generated_endpoint(
            {
                "request": {
                    "method": "GET",
                    "url": {
                        "raw": "https://api.exemplo.com/orders/{order_id}",
                        "protocol": "https",
                        "host": ["api", "exemplo", "com"],
                        "path": ["orders", "{order_id}"],
                        "variable": [],
                    },
                }
            },
            (),
        )
        assert "@pytest.mark.skip" in endpoint_skip.content  # pré-condição do cenário G

        suite_dir = _write_suite(
            tmp_path,
            "combined",
            [endpoint_a, endpoint_b, endpoint_c, endpoint_secure, endpoint_skip],
        )

        result = _run_real(
            suite_dir, server.base_url, monkeypatch_module, known_secret_values=(_SECRET,)
        )
        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-3000:]} {result.stderr[-3000:]}"
        )

        raw_payload, record, report, html = _persist_read_report_html(
            result, tmp_path / "run_combined"
        )
        return {
            "result": result,
            "raw_payload": raw_payload,
            "record": record,
            "report": report,
            "html": html,
        }
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def monkeypatch_module(request):
    # monkeypatch padrão é function-scoped; este fixture module-scoped
    # evita reconstruir o servidor/suíte/execução real (cara) uma vez por
    # cenário — a fixture combined_pipeline roda a execução real UMA
    # única vez para todo este módulo, mesmo padrão de MonkeyPatch usado
    # pela própria pytest internamente para escopos maiores.
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def _find_test_id(record, function_name_contains: str) -> str:
    matches = [
        t.test_id
        for t in record.http_transactions + record.assertion_results
        if function_name_contains in t.test_id
    ]
    assert matches, f"nenhum test_id contém {function_name_contains!r}"
    return matches[0]


# ============================================================================
# Cenário A — PASS completo
# ============================================================================


def test_scenario_a_pass_completo(combined_pipeline):
    record = combined_pipeline["record"]
    report = combined_pipeline["report"]
    html = combined_pipeline["html"]
    test_id = _find_test_id(record, "users_1")

    test_failure_names = {tf.test_name for tf in record.test_failures}
    assert test_id not in test_failure_names  # não existe test_failure

    transactions = [t for t in record.http_transactions if t.test_id == test_id]
    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.method == "GET"
    assert transaction.response_status == 200
    assert json.loads(transaction.response_body) == {"id": 1, "name": "Ana"}

    assertions = [a for a in record.assertion_results if a.test_id == test_id]
    assert assertions, "assertions existem"
    assert all(a.status == "PASSED" for a in assertions)

    evidence_failures = [e for e in record.evidence_failures if e.test_id == test_id]
    assert evidence_failures == []  # não existe evidence_failure

    trace_artifacts = [t for t in record.trace_artifacts if t.test_id == test_id]
    assert trace_artifacts == []  # PASS nunca gera trace (regra P1.3)

    report_test = next(t for t in report.execution.tests if t.test_id == test_id)
    assert len(report_test.transactions) == 1
    assert all(a.status == "PASSED" for a in report_test.assertions)
    assert report_test.trace is None
    assert report_test.evidence_failures == ()

    block = _block_for(html, test_id)
    assert 'class="status status-passed"' in block
    assert "Ver Trace" not in block
    assert "Infraestrutura de evidências" not in block
    assert "assertion-failed" not in block


# ============================================================================
# Cenário B — uma assertion falha
# ============================================================================


def test_scenario_b_uma_assertion_falha(combined_pipeline):
    record = combined_pipeline["record"]
    report = combined_pipeline["report"]
    html = combined_pipeline["html"]
    test_id = _find_test_id(record, "orders_1")

    test_failure = next(tf for tf in record.test_failures if tf.test_name == test_id)
    assert test_failure is not None

    status_result = _assertion_by_name(
        [a for a in record.assertion_results if a.test_id == test_id], "HTTP status"
    )
    assert status_result.status == "FAILED"
    assert status_result.expected == 201
    assert status_result.actual == 200

    # HTTP evidence continua preservada mesmo com a assertion falhando.
    transactions = [t for t in record.http_transactions if t.test_id == test_id]
    assert len(transactions) == 1
    assert transactions[0].response_status == 200

    # Demais assertions continuam sendo avaliadas (P2.2) — devem existir e
    # estar PASSED (schema/required batem com a resposta real).
    other_names = {
        a.name for a in record.assertion_results if a.test_id == test_id and a.name != "HTTP status"
    }
    assert other_names, "as demais assertions devem ter sido avaliadas"
    for a in record.assertion_results:
        if a.test_id == test_id and a.name != "HTTP status":
            assert a.status == "PASSED"

    # Nenhum evidence_failure artificial.
    assert [e for e in record.evidence_failures if e.test_id == test_id] == []

    # Trace: regra atual é gerar trace para todo teste que falhou —
    # confirmado/validado com detalhe no Cenário D (test_C), aqui só
    # confirma presença.
    assert any(t.test_id == test_id for t in record.trace_artifacts)

    report_test = next(t for t in report.execution.tests if t.test_id == test_id)
    assert any(a.status == "FAILED" for a in report_test.assertions)
    assert any(a.status == "PASSED" for a in report_test.assertions)

    block = _block_for(html, test_id)
    assert 'class="status status-failed"' in block
    assert "201" in block and "200" in block


# ============================================================================
# Cenário C — múltiplas assertions falham simultaneamente
# ============================================================================


def test_scenario_c_multiplas_assertions_falham(combined_pipeline):
    record = combined_pipeline["record"]
    report = combined_pipeline["report"]
    test_id = _find_test_id(record, "products_1")

    assertions = {a.name: a for a in record.assertion_results if a.test_id == test_id}
    assert assertions["HTTP status"].status == "FAILED"
    assert assertions["HTTP status"].expected == 201
    assert assertions["HTTP status"].actual == 200
    assert assertions["json_schema"].status == "FAILED"
    assert assertions["expected_value:id"].status == "FAILED"
    assert assertions["expected_value:id"].expected == 999
    assert assertions["expected_value:id"].actual == 123
    # required_field:name PASSA (name está presente) — prova de que uma
    # categoria reprovada não contamina outra independente.
    assert assertions["required_field:name"].status == "PASSED"

    # Nenhum expected/actual trocado entre categorias.
    assert assertions["HTTP status"].expected != assertions["expected_value:id"].expected
    assert all(a.test_id == test_id for a in assertions.values())

    # Exatamente UM test_failure para o teste — nunca um por assertion.
    test_failures_for_id = [tf for tf in record.test_failures if tf.test_name == test_id]
    assert len(test_failures_for_id) == 1

    assert record.success is False

    report_test = next(t for t in report.execution.tests if t.test_id == test_id)
    failed_count = sum(1 for a in report_test.assertions if a.status == "FAILED")
    passed_count = sum(1 for a in report_test.assertions if a.status == "PASSED")
    # HTTP status + json_schema + expected_value:id são 3 categorias
    # FAILED independentes (P2.2: nenhuma impede a avaliação da outra);
    # required_field:name e field_type:name são PASSED (o schema também
    # declara "name": {"type": "string"}).
    assert failed_count == 3
    assert passed_count == 2


# ============================================================================
# Cenário D — FAIL + HTTP evidence + trace
# ============================================================================


def test_scenario_d_fail_http_evidence_trace(combined_pipeline):
    record = combined_pipeline["record"]
    report = combined_pipeline["report"]
    html = combined_pipeline["html"]
    test_id_c = _find_test_id(record, "products_1")
    test_id_a = _find_test_id(record, "users_1")  # para checar isolamento abaixo

    test_failure = next(tf for tf in record.test_failures if tf.test_name == test_id_c)
    assert test_failure is not None

    transactions_c = [t for t in record.http_transactions if t.test_id == test_id_c]
    assert len(transactions_c) == 1

    assertion_failed = any(
        a.status == "FAILED" for a in record.assertion_results if a.test_id == test_id_c
    )
    assert assertion_failed

    traces_c = [t for t in record.trace_artifacts if t.test_id == test_id_c]
    assert len(traces_c) == 1
    assert traces_c[0].test_id == test_id_c

    # Nenhuma evidência de outro teste aparece no bloco de test_C: o
    # próprio agrupamento do ReportEngine (ReportTestExecution.transactions/
    # assertions) já é por test_id — ReportHttpTransaction/
    # ReportAssertionResult (os itens dentro do grupo) não carregam um
    # test_id próprio porque já estão dentro do grupo correto; a garantia
    # de isolamento está em quantos/quais itens aparecem em cada grupo.
    report_test_c = next(t for t in report.execution.tests if t.test_id == test_id_c)
    assert report_test_c.trace is not None and report_test_c.trace.test_id == test_id_c
    assert len(report_test_c.transactions) == 1
    # status + required_field:name + field_type:name + json_schema +
    # expected_value:id.
    assert len(report_test_c.assertions) == 5

    block_c = _block_for(html, test_id_c)
    assert "Ver Trace" in block_c
    block_a = _block_for(html, test_id_a)
    assert "Ver Trace" not in block_a
    # Nada de test_C aparece no bloco de test_A.
    assert "Widget" not in block_a
    assert "123" not in block_a or test_id_a not in block_a


# ============================================================================
# Cenário G — skipped
# ============================================================================


def test_scenario_g_skipped(combined_pipeline):
    result = combined_pipeline["result"]
    raw_payload = combined_pipeline["raw_payload"]
    record = combined_pipeline["record"]
    report = combined_pipeline["report"]
    html = combined_pipeline["html"]

    assert result.skipped_tests == 1
    assert raw_payload["summary"]["skipped"] == 1
    assert record.skipped_tests == 1
    assert report.execution.skipped_tests == 1

    def _card_value(html: str, label: str) -> str:
        marker = f'<p class="card-label">{label}</p>'
        before = html[: html.index(marker)]
        value_open = before.rindex('<p class="card-value">') + len('<p class="card-value">')
        value_close = before.index("</p>", value_open)
        return before[value_open:value_close]

    assert _card_value(html, "Skipped") == "1"

    # skipped não cria test_id, não vira assertion nem evidence_failure.
    skipped_ids = {t.test_id for t in report.execution.tests if "placeholder" in t.test_id.lower()}
    assert skipped_ids == set()
    assert "test_placeholder" not in {a.test_id for a in record.assertion_results}
    assert "test_placeholder" not in {e.test_id for e in record.evidence_failures}
    # skipped não altera Passed/Failed (contadores de assertions, não de
    # testes) — confirmado indiretamente: nenhuma assertion nova apareceu
    # com esse test_id.
    assert not any(a.test_id == "test_placeholder" for a in record.assertion_results)


# ============================================================================
# Cenário H — isolamento entre testes
# ============================================================================


def test_scenario_h_isolamento_entre_testes(combined_pipeline):
    record = combined_pipeline["record"]
    html = combined_pipeline["html"]
    test_a = _find_test_id(record, "users_1")  # PASS, HTTP evidence, sem trace
    test_c = _find_test_id(record, "products_1")  # FAIL, assertion failure, trace

    assert test_a != test_c

    trace_ids = {t.test_id for t in record.trace_artifacts}
    assert test_c in trace_ids
    assert test_a not in trace_ids  # trace de C nunca aparece em A

    transactions_a = [t for t in record.http_transactions if t.test_id == test_a]
    transactions_c = [t for t in record.http_transactions if t.test_id == test_c]
    assert transactions_a and transactions_c
    assert transactions_a[0].response_body != transactions_c[0].response_body

    assertions_a_names = {a.name for a in record.assertion_results if a.test_id == test_a}
    assertions_c_names = {a.name for a in record.assertion_results if a.test_id == test_c}
    # "expected_value:id"/"json_schema" FAILED de C nunca aparecem
    # associados a A (test_a não declarou SCHEMA com const).
    assert "expected_value:id" not in assertions_a_names

    test_failure_names = {tf.test_name for tf in record.test_failures}
    assert test_c in test_failure_names
    assert test_a not in test_failure_names  # test_failure de C nunca aparece em A

    block_a = _block_for(html, test_a)
    block_c = _block_for(html, test_c)
    assert test_c not in block_a
    assert test_a not in block_c


# ============================================================================
# Cenário I — round trip (ExecutionResult -> result.json -> Reader; depois
# Reader -> ReportEngine -> HTML), usando o cenário mais rico (test_C).
# ============================================================================


def test_scenario_i_round_trip(combined_pipeline):
    result = combined_pipeline["result"]
    record = combined_pipeline["record"]
    raw_payload = combined_pipeline["raw_payload"]

    assert record.success == result.success
    assert len(record.test_failures) == len(result.test_failures)
    assert len(record.assertion_results) == len(result.assertion_results)
    assert len(record.http_transactions) == len(result.http_transactions)
    assert len(record.trace_artifacts) == len(result.trace_artifacts)
    assert len(record.evidence_failures) == len(result.evidence_failures)
    assert record.skipped_tests == result.skipped_tests

    # query_parameters preservados (P2.1) — test_secure tem "token".
    secure_transaction_before = next(
        t for t in result.http_transactions if "secure_echo" in t.test_id
    )
    secure_transaction_after = next(
        t for t in record.http_transactions if "secure_echo" in t.test_id
    )
    before_params = {p.name: p.value for p in secure_transaction_before.query_parameters}
    after_params = {p.name: p.value for p in secure_transaction_after.query_parameters}
    assert before_params == after_params
    assert "token" in before_params

    # ExecutionResult não tem exit_code diretamente comparável a record
    # (record não expõe exit_code) — comparado via raw_payload/summary.
    assert raw_payload["success"] == result.success
    assert raw_payload["summary"]["requests"] == result.total_requests
    assert raw_payload["summary"]["assertions"] == result.total_assertions
    assert raw_payload["summary"]["failed"] == result.failed_assertions


# ============================================================================
# Cenário J — masking (usa o mesmo test_secure da suíte combinada)
# ============================================================================


def test_scenario_j_masking(combined_pipeline):
    result = combined_pipeline["result"]
    raw_payload = combined_pipeline["raw_payload"]
    html = combined_pipeline["html"]

    assert _SECRET not in result.stdout
    assert _SECRET not in result.stderr

    secure_transaction = next(t for t in result.http_transactions if "secure_echo" in t.test_id)
    assert _SECRET not in json.dumps(
        [
            secure_transaction.request_body,
            secure_transaction.response_body,
            [(h.name, h.value) for h in secure_transaction.request_headers],
            [(h.name, h.value) for h in secure_transaction.response_headers],
            [(p.name, p.value) for p in secure_transaction.query_parameters],
        ]
    )
    assert _MASKED_SECRET in (secure_transaction.request_body or "")
    assert _MASKED_SECRET in (secure_transaction.response_body or "")
    assert any(_MASKED_SECRET in h.value for h in secure_transaction.response_headers)
    assert any(_MASKED_SECRET in p.value for p in secure_transaction.query_parameters)

    for tf in result.test_failures:
        assert _SECRET not in tf.error_message
    for ef in result.evidence_failures:
        assert _SECRET not in ef.message

    assert _SECRET not in json.dumps(raw_payload)
    assert _MASKED_SECRET in json.dumps(raw_payload)
    assert _SECRET not in html
    assert _MASKED_SECRET in html


# ============================================================================
# Cenário E — FAIL + evidence_failure (execução real independente, para
# que o trace real ainda não tenha sido movido por outra persistência).
# ============================================================================


def test_scenario_e_fail_com_evidence_failure(tmp_path, monkeypatch):
    server = PostmanTestServer()
    try:
        server.set_route("/orders/2", method="GET", status=200, body={"id": 2, "status": "placed"})
        endpoint = _generated_endpoint(
            {"request": {"method": "GET", "url": "https://api.exemplo.com/orders/2"}},
            (_status_assertion(201),),  # real: 200 -> FAILED -> gera trace real
        )
        suite_dir = _write_suite(tmp_path, "evidence_failure", [endpoint])
        result = _run_real(suite_dir, server.base_url, monkeypatch)
        assert result.infrastructure_failure is None
        assert len(result.trace_artifacts) == 1
        assert result.test_failures  # falha funcional real, independente da evidência

        # Fronteira mockada (documentada): shutil.move falha ao mover o
        # trace já capturado, simulando um problema real de I/O na
        # persistência — nunca mocka InfrastructureFailure/evidence_
        # failures em si, só a chamada de sistema.
        monkeypatch.setattr(shutil, "move", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio (simulado)")))

        run_dir = tmp_path / "run_evidence_failure"
        raw_payload, record, report, html = _persist_read_report_html(result, run_dir)

        test_id = result.test_failures[0].test_name

        # test_failure continua representando a falha FUNCIONAL original.
        assert any(tf.test_name == test_id for tf in record.test_failures)
        assert record.success is False

        # evidence_failure representa exclusivamente a falha de
        # infraestrutura da evidência — categoria separada.
        assert len(record.evidence_failures) == 1
        evidence_failure = record.evidence_failures[0]
        assert evidence_failure.test_id == test_id
        assert evidence_failure.source == "playwright_trace"
        assert evidence_failure.failure_type == InfrastructureFailureType.EVIDENCE_PERSISTENCE_FAILED

        # trace_artifacts não contém referência inválida: o trace que
        # falhou ao mover não pode aparecer como se tivesse sido
        # persistido com sucesso.
        assert record.trace_artifacts == ()

        # A falha funcional original não foi substituída pela falha de
        # evidência (duas entradas semanticamente distintas).
        assert record.test_failures[0].test_name == test_id
        assert record.test_failures[0].error_message  # preservada, não sobrescrita

        report_test = next(t for t in report.execution.tests if t.test_id == test_id)
        assert len(report_test.evidence_failures) == 1
        assert any(a.status == "FAILED" for a in report_test.assertions)

        block = _block_for(html, test_id)
        assert 'class="status status-failed"' in block
        assert "Infraestrutura de evidências" in block
        # evidence_failure nunca é apresentado como assertion.
        assert "Falha ao mover" in block or "disco cheio" in block or evidence_failure.message in block
    finally:
        server.shutdown()


# ============================================================================
# Cenário F — PASS + evidence_failure
#
# ACHADO (ver relatório final): a infraestrutura REAL não tem, hoje,
# nenhum jeito de produzir uma evidence_failure para um teste que PASSOU
# — evidence_failures hoje só se origina de uma falha ao capturar/mascarar/
# mover um Trace (source="playwright_trace"), e Trace só é gerado para um
# teste que FALHOU (regra explícita do P1.3). Este cenário é, portanto,
# construído diretamente sobre um ExecutionResult (nunca uma segunda
# implementação de captura de evidência) — a fronteira mockada aqui é a
# ORIGEM da evidence_failure (sintética, documentada), nunca o pipeline
# posterior (persistência/leitura/relatório/HTML, todos reais).
# ============================================================================


def test_scenario_f_pass_com_evidence_failure(tmp_path):
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
                test_id="test_pass_evidence_failure",
                method="GET",
                url="https://api.exemplo.com/status",
                request_headers=(),
                request_body=None,
                response_status=200,
                response_headers=(),
                response_body='{"ok": true}',
            ),
        ),
        assertion_results=(
            AssertionResult(
                test_id="test_pass_evidence_failure",
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
                test_id="test_pass_evidence_failure",
            ),
        ),
    )

    run_dir = tmp_path / "run_f"
    raw_payload, record, report, html = _persist_read_report_html(result, run_dir)

    assert result.success is True
    assert result.evidence_failures != ()

    assert record.success is True
    assert record.test_failures == ()  # nenhuma test_failure criada artificialmente

    report_test = report.execution.tests[0]
    assert report_test.test_id == "test_pass_evidence_failure"
    assert len(report_test.evidence_failures) == 1
    assert all(a.status == "PASSED" for a in report_test.assertions)

    block = _block_for(html, "test_pass_evidence_failure")
    assert 'class="status status-passed"' in block
    assert 'class="status status-failed"' not in block
    assert "Infraestrutura de evidências" in block
    assert "assertion assertion-failed" not in block
