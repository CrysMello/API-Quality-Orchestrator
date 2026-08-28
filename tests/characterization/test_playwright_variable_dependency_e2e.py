"""Teste de caracterização E2E — dependências e ordem de execução entre
endpoints Playwright (VariableUsage/VariableExtraction, ver
endpoint_dependency_linking.py).

Cadeia validada, ponta a ponta, com componentes REAIS (nunca mockados):

    Collection -> PostmanCollectionParser -> ApiAnalysisEngine
    -> TestStrategyEngine (VariableExtraction) -> endpoint_dependency_linking
    (VariableUsage, ordem, ciclos) -> PlaywrightEndpointTestGenerator
    -> DefaultPlaywrightTestSuiteBuilder (01_/02_/03_.../generation-manifest.json)
    -> arquivos .py físicos -> Playwright real + pytest real + servidor
    HTTP real (tests/postman_test_server.py) -> PlaywrightAdapter
    -> ExecutionResult -> PersistExecutionResultUseCase -> result.json
    -> JsonExecutionResultReader -> ReportEngine -> HTML

FRONTEIRAS MOCKADAS (documentadas explicitamente, nunca o objeto sob
validação): apenas o repositório de persistência do result.json
(`_RealFileRepository`, grava em tmp_path real — mesmo padrão já usado em
todo o restante desta suíte de caracterização). Playwright, pytest e o
servidor HTTP são sempre reais; LocalArtifactRepository (real) grava os
.py físicos da suíte gerada.

NÃO altera Postman/Newman (fora do escopo deste arquivo por completo — ver
tests/unit/test_postman_generator_unaffected_by_variable_dependencies.py
para a confirmação explícita disso).

Documenta o comportamento ATUAL — se quebrar por uma mudança deliberada,
atualize-o conscientemente (ver tests/characterization/README.md).
"""

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api_quality_agent.adapters.filesystem import JsonExecutionResultReader, LocalArtifactRepository
from api_quality_agent.adapters.playwright import PlaywrightAdapter
from api_quality_agent.application.use_cases import (
    GeneratePlaywrightTestSuiteUseCase,
    PersistExecutionResultUseCase,
)
from api_quality_agent.domain.models import ExecutionResultLocation
from api_quality_agent.domain.services import (
    ApiAnalysisEngine,
    InferenceSchemaProvider,
    SchemaInferenceEngine,
    TestStrategyEngine,
)
from api_quality_agent.generators.playwright import (
    DefaultPlaywrightTestSuiteBuilder,
    PlaywrightEndpointTestGenerator,
)
from api_quality_agent.parsers import PostmanCollectionParser
from api_quality_agent.reporting import ReportEngine, render_execution_report_html
from postman_test_server import PostmanTestServer

_STARTED_AT = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 28, 12, 1, 0, tzinfo=timezone.utc)


class _RealFileRepository:
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def save(self, *, content: str) -> ExecutionResultLocation:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / "result.json"
        path.write_text(content, encoding="utf-8")
        return ExecutionResultLocation(path=str(path))


def _json_example(code: int, status: str, body: dict) -> dict:
    return {
        "name": status,
        "status": status,
        "code": code,
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": json.dumps(body),
    }


def _customer_path_url(raw_suffix: str = "") -> dict:
    return {
        "raw": f"https://api.exemplo.com/customers/{{customer_id}}{raw_suffix}",
        "protocol": "https",
        "host": ["api", "exemplo", "com"],
        "path": ["customers", "{customer_id}"],
        "variable": [],
    }


def _chain_document():
    # Ordem deliberada da Collection: produtor primeiro, os DOIS
    # consumidores do mesmo valor em seguida (Etapa 7, múltiplos
    # consumidores), e por fim um endpoint totalmente independente (Etapa
    # 9) — a ordem final calculada pela linkagem preserva exatamente esta
    # ordem (produtor já vem antes de quem depende dele).
    items = [
        {
            "name": "Create customer",
            "id": "post-customers",
            "request": {"method": "POST", "url": "https://api.exemplo.com/customers"},
            "response": [_json_example(201, "Created", {"customer_id": 123, "name": "Ada"})],
        },
        {
            "name": "Get customer",
            "id": "get-customer",
            # Deliberadamente SEM nenhum campo "*_id"/"id" no corpo de
            # resposta deste consumidor — se tivesse, o próprio
            # TestStrategyEngine o tornaria TAMBÉM um "produtor" de
            # customer_id (mesma heurística de qualquer outro endpoint,
            # ver _find_variable_extraction_candidates), e o consumidor
            # seguinte (PUT) ligaria à sua vizinhança mais próxima (este
            # GET) em vez do produtor real (POST) — cenário de ambiguidade
            # deliberadamente evitado aqui, não uma limitação escondida
            # (ver test_endpoint_dependency_linking.py para a regra de
            # "produtor mais próximo" em isolamento).
            "request": {"method": "GET", "url": _customer_path_url()},
            "response": [_json_example(200, "OK", {"status": "active"})],
        },
        {
            "name": "Update customer",
            "id": "put-customer",
            "request": {"method": "PUT", "url": _customer_path_url()},
            "response": [_json_example(200, "OK", {"updated": True})],
        },
        {
            "name": "List products",
            "id": "get-products",
            "request": {"method": "GET", "url": "https://api.exemplo.com/products"},
            "response": [_json_example(200, "OK", {"items": []})],
        },
    ]
    return PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Customer Chain",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": items,
            }
        )
    )


def _build_use_case(artifacts_path: Path, *, id_factory) -> GeneratePlaywrightTestSuiteUseCase:
    # Pipeline REAL completo (nenhum componente mockado) — a mesma
    # fábrica usada por GenerateTestsFromDocumentUseCase/CLI em produção.
    return GeneratePlaywrightTestSuiteUseCase(
        ApiAnalysisEngine(),
        InferenceSchemaProvider(SchemaInferenceEngine()),
        TestStrategyEngine(),
        PlaywrightEndpointTestGenerator(),
        DefaultPlaywrightTestSuiteBuilder(),
        LocalArtifactRepository(artifacts_path),
        id_factory=id_factory,
    )


def _generate_suite(tmp_path: Path, *, exec_id: str, document=None) -> Path:
    use_case = _build_use_case(tmp_path / "artifacts", id_factory=lambda: exec_id)
    use_case.execute(document=document or _chain_document(), collection_id="col-1")
    suite_dir = tmp_path / "artifacts" / "local" / "col-1" / exec_id / "scripts" / "playwright"
    assert suite_dir.is_dir()
    return suite_dir


def _run_real(suite_dir: Path, base_url: str, monkeypatch, *, timeout: float = 120.0):
    monkeypatch.setenv("PLAYWRIGHT_BASE_URL", base_url)
    adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
    return adapter.run(tests_path=str(suite_dir), timeout_seconds=timeout)


def _persist_read_report_html(result, run_dir: Path):
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))
    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="Customer Chain",
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


@pytest.fixture(scope="module")
def monkeypatch_module(request):
    # monkeypatch padrão é function-scoped; este fixture module-scoped
    # evita reconstruir servidor/suíte/execução real (cara) mais de uma
    # vez para os testes que só LEEM o resultado da mesma execução — mesmo
    # padrão já usado por test_playwright_p2_pipeline_sanity.py.
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def chain_pipeline(tmp_path_factory, monkeypatch_module):
    tmp_path = tmp_path_factory.mktemp("dependency_chain")
    server = PostmanTestServer()
    try:
        server.set_route(
            "/customers", method="POST", status=201, body={"customer_id": 123, "name": "Ada"}
        )
        server.set_route(
            "/customers/123", method="GET", status=200, body={"customer_id": 123, "status": "active"}
        )
        server.set_route(
            "/customers/123", method="PUT", status=200, body={"customer_id": 123, "updated": True}
        )
        server.set_route("/products", method="GET", status=200, body={"items": []})

        suite_dir = _generate_suite(tmp_path, exec_id="exec-chain")
        manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))

        result = _run_real(suite_dir, server.base_url, monkeypatch_module)
        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-3000:]} {result.stderr[-3000:]}"
        )

        raw_payload, record, report, html = _persist_read_report_html(result, tmp_path / "run")
        return {
            "suite_dir": suite_dir,
            "manifest": manifest,
            "result": result,
            "raw_payload": raw_payload,
            "record": record,
            "report": report,
            "html": html,
            "received_paths": list(server.received_paths),
        }
    finally:
        server.shutdown()


# ============================================================================
# Etapa 7 — ordem/prefixo numérico calculado a partir das dependências
# ============================================================================


def test_files_are_generated_with_numeric_prefixes_in_dependency_order(chain_pipeline):
    suite_dir = chain_pipeline["suite_dir"]

    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))

    assert file_names == [
        "01_test_post_customers.py",
        "02_test_get_customers_by_customer_id.py",
        "03_test_put_customers_by_customer_id.py",
        "04_test_get_products.py",
    ]


def test_manifest_reports_the_variable_dependencies_between_the_files(chain_pipeline):
    manifest = chain_pipeline["manifest"]

    assert manifest["schema_version"] == "1.3"
    dependencies = manifest["variable_dependencies"]
    # Etapa 7 (múltiplos consumidores): GET e PUT, os dois consumindo a
    # MESMA variável do MESMO produtor.
    assert len(dependencies) == 2
    assert {entry["variable"] for entry in dependencies} == {"customer_id"}
    assert {entry["consumer"] for entry in dependencies} == {
        "GET /customers/{customer_id}",
        "PUT /customers/{customer_id}",
    }
    producer_ids = {entry["producer_test_id"] for entry in dependencies}
    assert producer_ids == {"test_post_customers_success"}
    assert all(entry["location"] == "path" for entry in dependencies)


# ============================================================================
# Etapa 5/6 — código gerado (extração no produtor, resolução em runtime no
# consumidor) é sintaticamente válido e nunca contém o placeholder cru
# ============================================================================


def test_generated_files_are_valid_python_and_never_leak_the_raw_placeholder(chain_pipeline):
    suite_dir = chain_pipeline["suite_dir"]
    for py_file in (suite_dir / "endpoints").glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        ast.parse(content)
        if "customers" in py_file.name and "post" not in py_file.name:
            # A forma PROIBIDA é o placeholder cru como literal (sem "f"
            # na frente) — a forma f"/customers/{customer_id}" (dinâmica,
            # resolvida em runtime) é exatamente o esperado e NUNCA deve
            # ser confundida com ela (mesma armadilha de substring já
            # corrigida em P2.5: 'f"..."' sempre contém '"..."' como
            # substring).
            assert 'api_context.get("/customers/{customer_id}")' not in content
            assert 'api_context.put("/customers/{customer_id}")' not in content
            assert '"/customers/"' not in content
            assert "_get_shared_variable(" in content
            assert 'f"/customers/{customer_id}"' in content


# ============================================================================
# Teste 4 (Etapa 10) — E2E real: POST -> extract id -> runtime state
# -> GET /customers/123 -> PUT /customers/123, tudo real
# ============================================================================


def test_e2e_producer_extracts_and_both_consumers_receive_the_produced_value(chain_pipeline):
    result = chain_pipeline["result"]
    record = chain_pipeline["record"]

    assert result.success is True  # cadeia inteira passou

    # Prova definitiva do encadeamento: GET e PUT chegaram ao servidor com
    # o customer_id REAL devolvido pelo POST (123) — nunca um valor
    # inventado, nunca a URL crua com o placeholder, nunca vazia.
    get_transaction = next(t for t in record.http_transactions if t.method == "GET" and "customers" in t.url)
    assert get_transaction.url.endswith("/customers/123")
    assert get_transaction.response_status == 200

    put_transaction = next(t for t in record.http_transactions if t.method == "PUT")
    assert put_transaction.url.endswith("/customers/123")
    assert put_transaction.response_status == 200

    post_transaction = next(t for t in record.http_transactions if t.method == "POST")
    assert post_transaction.response_status == 201
    assert json.loads(post_transaction.response_body)["customer_id"] == 123

    assert "/customers/123" in chain_pipeline["received_paths"]


# ============================================================================
# Teste 9 (Etapa 10) — endpoint independente continua funcionando
# ============================================================================


def test_independent_endpoint_is_unaffected_by_the_dependency_chain(chain_pipeline):
    record = chain_pipeline["record"]
    manifest = chain_pipeline["manifest"]

    products_transaction = next(t for t in record.http_transactions if t.url.endswith("/products"))
    assert products_transaction.response_status == 200

    products_entry = next(entry for entry in manifest["endpoints"] if entry["path"] == "/products")
    assert products_entry["rendered"] is True
    assert products_entry["coverage"] in ("complete", "partial")

    # Nenhuma VariableUsage foi atribuída a este endpoint — nunca
    # artificialmente transformado em dependência (Etapa 7, regra
    # explícita).
    dependency_consumers = {entry["consumer"] for entry in manifest["variable_dependencies"]}
    assert "GET /products" not in dependency_consumers


# ============================================================================
# Teste 10 (Etapa 10) — round trip completo do relatório para a cadeia real
# ============================================================================


def test_round_trip_result_json_and_html_reflect_the_real_chain(chain_pipeline):
    raw_payload = chain_pipeline["raw_payload"]
    html = chain_pipeline["html"]

    assert raw_payload["success"] is True
    assert "/customers/123" in json.dumps(raw_payload)
    assert "123" in html


# ============================================================================
# Teste 5 (Etapa 10) — produtor falha -> consumidor falha explicitamente,
# nunca uma chamada HTTP inválida
# ============================================================================


def test_producer_failure_makes_the_consumer_fail_explicitly_never_calling_with_an_invalid_value(
    tmp_path, monkeypatch
):
    server = PostmanTestServer()
    try:
        # Deliberadamente SEM rota para POST /customers -> 404 real (nunca
        # simulado) -> o produtor falha (201 esperado) e a extração também
        # (corpo do 404 não tem "id") -> nada é armazenado.
        server.set_route(
            "/customers/123", method="GET", status=200, body={"customer_id": 123}
        )
        server.set_route("/customers/123", method="PUT", status=200, body={"customer_id": 123})
        server.set_route("/products", method="GET", status=200, body={"items": []})

        suite_dir = _generate_suite(tmp_path, exec_id="exec-producer-fails")
        result = _run_real(suite_dir, server.base_url, monkeypatch)

        assert result.infrastructure_failure is None
        assert result.success is False  # produtor (e portanto a suíte) falhou

        producer_failure = next(
            tf for tf in result.test_failures if "post_customers" in tf.test_name
        )
        assert producer_failure is not None

        consumer_failure = next(
            tf for tf in result.test_failures if "get_customers" in tf.test_name
        )
        assert "customer_id" in consumer_failure.error_message
        assert "indisponível" in consumer_failure.error_message
        assert "test_post_customers_success" in consumer_failure.error_message

        # Nenhuma chamada HTTP inválida: o servidor real nunca recebeu uma
        # requisição para /customers/123 (o único valor que existiria SE a
        # extração tivesse funcionado) nem para um path vazio/placeholder.
        assert "/customers/123" not in server.received_paths
        assert "/customers/" not in server.received_paths
        assert "/customers/None" not in server.received_paths
    finally:
        server.shutdown()


# ============================================================================
# Teste 6 (Etapa 10) — isolamento real entre duas cadeias independentes que
# usam o MESMO nome de variável
# ============================================================================


def _isolation_document():
    def _url(prefix: str, raw_suffix: str = "") -> dict:
        return {
            "raw": f"https://api.exemplo.com/{prefix}/{{resource_id}}{raw_suffix}",
            "protocol": "https",
            "host": ["api", "exemplo", "com"],
            "path": [prefix, "{resource_id}"],
            "variable": [],
        }

    items = [
        {
            "name": "Create alpha",
            "id": "post-alpha",
            "request": {"method": "POST", "url": "https://api.exemplo.com/alpha"},
            "response": [_json_example(201, "Created", {"resource_id": 111})],
        },
        {
            "name": "Get alpha",
            "id": "get-alpha",
            "request": {"method": "GET", "url": _url("alpha")},
            "response": [_json_example(200, "OK", {"resource_id": 111})],
        },
        {
            "name": "Create beta",
            "id": "post-beta",
            "request": {"method": "POST", "url": "https://api.exemplo.com/beta"},
            "response": [_json_example(201, "Created", {"resource_id": 222})],
        },
        {
            "name": "Get beta",
            "id": "get-beta",
            "request": {"method": "GET", "url": _url("beta")},
            "response": [_json_example(200, "OK", {"resource_id": 222})],
        },
    ]
    return PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Isolation",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": items,
            }
        )
    )


def test_two_independent_chains_sharing_a_variable_name_never_cross_contaminate(
    tmp_path, monkeypatch
):
    server = PostmanTestServer()
    try:
        server.set_route("/alpha", method="POST", status=201, body={"resource_id": 111})
        server.set_route("/alpha/111", method="GET", status=200, body={"resource_id": 111})
        server.set_route("/beta", method="POST", status=201, body={"resource_id": 222})
        server.set_route("/beta/222", method="GET", status=200, body={"resource_id": 222})

        suite_dir = _generate_suite(
            tmp_path, exec_id="exec-isolation", document=_isolation_document()
        )
        result = _run_real(suite_dir, server.base_url, monkeypatch)

        assert result.infrastructure_failure is None
        assert result.success is True, (
            f"cadeias deveriam passar isoladamente: {result.stdout[-3000:]}"
        )

        # A prova real de isolamento: o consumidor de alpha SÓ chamou
        # /alpha/111 (nunca /alpha/222), e o de beta SÓ chamou /beta/222
        # (nunca /beta/111) — mesmo os dois produtores usando o MESMO nome
        # de variável ("resource_id").
        assert "/alpha/111" in server.received_paths
        assert "/beta/222" in server.received_paths
        assert "/alpha/222" not in server.received_paths
        assert "/beta/111" not in server.received_paths
    finally:
        server.shutdown()
