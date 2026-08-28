"""P3.2 — E2E do fluxo REAL da CLI para dependências entre endpoints
Playwright: `generate` -> `run` -> `report`, ponta a ponta.

Diferença deliberada em relação aos arquivos de caracterização anteriores:

- test_playwright_variable_dependency_e2e.py entra por
  GeneratePlaywrightTestSuiteUseCase/PlaywrightAdapter montados à mão.
- test_playwright_variable_dependency_application_integration.py entra por
  `main(["generate", ...])` real, mas executa via PlaywrightAdapter direto.
- ESTE arquivo entra pelos TRÊS comandos reais da CLI
  (`api_quality_agent.cli.main.main`) — generate, run E report — a MESMA
  função que um usuário invoca no terminal, sem montar nenhum use
  case/adapter à mão quando o ambiente permite.

Cadeia validada, ponta a ponta:

    Collection (.json real em disco)
    -> main(["generate", "--target", "playwright", ...])
    -> arquivos .py físicos + generation-manifest.json
    -> main(["run", "--target", "playwright", ...])
    -> pytest/Playwright reais + servidor HTTP real
    -> shared-variables.ndjson (runtime, interno ao PlaywrightAdapter)
    -> result.json persistido por PersistExecutionResultUseCase
    -> main(["report", "--input", ...])
    -> HTML real (ReportEngine)

DESVIO DOCUMENTADO (run --target playwright): a flag --pytest-executable
da CLI aceita só um único executável (sem argumentos extras — não há como
passar "-m pytest" por ela). Este ambiente de sandbox não tem "pytest" no
PATH do sistema, mas TEM um console-script real instalado no site-packages
de usuário (localizado de forma portátil via site.getusersitepackages(),
nunca um caminho fixo de usuário/máquina) — quando encontrado, `run` é
disparado pelo `main()` real, SEM nenhum desvio. Se não for encontrado
(outro ambiente/CI), o fallback documentado é PlaywrightAdapter direto
(MESMA classe composta por bootstrap.build_offline_playwright_run_context)
com sys.executable + command_prefix=("-m", "pytest") — nunca uma segunda
infraestrutura de execução — seguido da MESMA persistência
(PersistExecutionResultUseCase) usada pela CLI, para que `report` via
`main()` continue funcionando de ponta a ponta nos dois casos.

FRONTEIRAS MOCKADAS: nenhuma. Generator, UseCase, Adapter, Builder e o
runtime state (shared-variables.ndjson) são sempre os reais. O servidor
HTTP é o real já existente em tests/postman_test_server.py. Nenhuma
segunda infraestrutura de execução foi criada.

Documenta o comportamento ATUAL — se quebrar por uma mudança deliberada,
atualize-o conscientemente (ver tests/characterization/README.md). Por
instrução explícita da tarefa (P3.2), nenhum bug encontrado aqui foi
corrigido — só documentado no relatório final da tarefa.
"""

import json
import shutil
import site
import sys
from pathlib import Path

from api_quality_agent.cli.exit_codes import SUCCESS
from api_quality_agent.cli.main import main
from postman_test_server import PostmanTestServer


def _find_real_pytest_executable() -> str | None:
    # Só para permitir `run --target playwright` via main() SEM desvio
    # nenhum, quando o ambiente realmente tiver um console-script pytest
    # utilizável (a flag --pytest-executable da CLI não aceita
    # command_prefix). Nunca um caminho fixo de usuário/máquina: PATH
    # primeiro, depois o Scripts/ irmão do site-packages de usuário
    # (mecanismo portátil do próprio módulo `site`), depois o Scripts/ ao
    # lado do próprio interpretador.
    found = shutil.which("pytest")
    if found:
        return found
    for candidate_dir in (
        Path(site.getusersitepackages()).parent / "Scripts",
        Path(sys.executable).with_name("Scripts"),
    ):
        candidate = candidate_dir / "pytest.exe"
        if candidate.is_file():
            return str(candidate)
    return None


_REAL_PYTEST_EXECUTABLE = _find_real_pytest_executable()


def _json_example(code: int, status: str, body: dict) -> dict:
    return {
        "name": status,
        "status": status,
        "code": code,
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": json.dumps(body),
    }


def _write_collection(path: Path, items: list) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    document = {
        "info": {
            "name": "P3.2",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }
    file_path = path / "collection.json"
    file_path.write_text(json.dumps(document), encoding="utf-8")
    return file_path


def _generate_playwright(tmp_path: Path, monkeypatch, collection_file: Path) -> Path:
    # PONTO REAL da aplicação: o mesmo main() de um terminal de verdade,
    # composto por bootstrap.build_offline_context() (mesma composição de
    # produção) — nenhum use case/adapter montado à mão aqui.
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    exit_code = main(["generate", "--file", str(collection_file), "-y", "--target", "playwright"])
    assert exit_code == SUCCESS

    suite_dirs = list(tmp_path.glob("artifacts/local/*/*/scripts/playwright"))
    assert len(suite_dirs) == 1, f"suíte não encontrada em artifacts/: {suite_dirs}"
    return suite_dirs[0]


def _run_playwright(
    suite_dir: Path, base_url: str, monkeypatch, tmp_path: Path
) -> tuple[int, dict]:
    monkeypatch.setenv("PLAYWRIGHT_BASE_URL", base_url)

    if _REAL_PYTEST_EXECUTABLE is not None:
        # SEM desvio: main() real de ponta a ponta.
        exit_code = main(
            [
                "run",
                "--target",
                "playwright",
                "--file",
                str(suite_dir),
                "--pytest-executable",
                _REAL_PYTEST_EXECUTABLE,
            ]
        )
    else:
        # DESVIO DOCUMENTADO (ver docstring do módulo): PlaywrightAdapter
        # direto (mesma classe da CLI) + persistência real (mesma classe
        # da CLI), porque --pytest-executable não aceita "-m pytest" e
        # este ambiente não tem um console-script pytest avulso.
        from api_quality_agent.adapters.filesystem import JsonExecutionResultRepository
        from api_quality_agent.adapters.playwright import PlaywrightAdapter
        from api_quality_agent.application.use_cases import PersistExecutionResultUseCase
        from datetime import datetime

        adapter = PlaywrightAdapter(
            pytest_executable=sys.executable, command_prefix=("-m", "pytest")
        )
        started_at = datetime.now()
        result = adapter.run(tests_path=str(suite_dir), timeout_seconds=120.0)
        finished_at = datetime.now()
        persist_use_case = PersistExecutionResultUseCase(JsonExecutionResultRepository())
        persist_use_case.execute(
            result,
            collection_id=None,
            collection_name=suite_dir.name,
            started_at=started_at,
            finished_at=finished_at,
            workspace_id=None,
            workspace_name=None,
        )
        exit_code = SUCCESS if result.success else 1

    matches = sorted(
        tmp_path.glob("artifacts/run_*/result.json"), key=lambda p: p.stat().st_mtime
    )
    assert matches, "nenhum result.json foi persistido pelo run"
    result_json_path = matches[-1]
    payload = json.loads(result_json_path.read_text(encoding="utf-8"))
    return exit_code, payload, result_json_path


def _report_html(result_json_path: Path, monkeypatch) -> str:
    # PONTO REAL da aplicação: main(["report", ...]) real, composto por
    # bootstrap.build_report_context().
    exit_code = main(["report", "--input", str(result_json_path), "--overwrite"])
    assert exit_code == SUCCESS
    html_path = result_json_path.parent / "report.html"
    assert html_path.is_file()
    return html_path.read_text(encoding="utf-8")


def _endpoint_file(suite_dir: Path, name_contains: str) -> str:
    matches = [p for p in (suite_dir / "endpoints").glob("*.py") if name_contains in p.name]
    assert len(matches) == 1, f"esperado 1 arquivo contendo {name_contains!r}, achei {matches}"
    return matches[0].read_text(encoding="utf-8")


# ============================================================================
# CENÁRIO 1 — fluxo CLI completo com dependência, EXATAMENTE como
# especificado na tarefa (response do POST é {"id": 123, "name": ...} —
# campo chamado "id", enquanto o path do consumidor usa "{customer_id}").
#
# ACHADO (ver relatório da tarefa, não corrigido aqui por instrução
# explícita): TestStrategyEngine._find_variable_extraction_candidates usa
# o NOME LITERAL do campo da resposta como variable_name da
# VariableExtraction (variable_name="id" para um campo "id"). A linkagem
# (endpoint_dependency_linking._nearest_producer) só casa um segmento de
# path parametrizado com um produtor cujo variable_name seja EXATAMENTE
# igual à chave do segmento — "id" != "customer_id", então NENHUM
# VariableUsage é criado: o GET cai no mesmo fallback de sempre para
# variável não resolvida (PlaceholderEndpointTestGenerator, @pytest.mark.
# skip), nunca na cadeia produtor/consumidor. Nem 01_/02_ nem shared state
# acontecem. test_scenario1b (abaixo) prova a mesma cadeia funcionando de
# ponta a ponta quando o campo da resposta já se chama "customer_id".
# ============================================================================


def test_scenario1_cli_full_pipeline_generate_run_report(tmp_path, monkeypatch):
    items = [
        {
            "name": "Create customer",
            "id": "post-customers",
            "request": {"method": "POST", "url": "https://api.exemplo.com/customers"},
            "response": [_json_example(201, "Created", {"id": 123, "name": "Test Customer"})],
        },
        {
            "name": "Get customer",
            "id": "get-customer",
            "request": {
                "method": "GET",
                "url": {
                    "raw": "https://api.exemplo.com/customers/{customer_id}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["customers", "{customer_id}"],
                    "variable": [],
                },
            },
            "response": [_json_example(200, "OK", {"status": "active"})],
        },
    ]
    collection_file = _write_collection(tmp_path / "collection", items)
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    # 1/2/3 — arquivos físicos, conteúdo, ordem.
    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))

    server = PostmanTestServer()
    try:
        server.set_route(
            "/customers", method="POST", status=201, body={"id": 123, "name": "Test Customer"}
        )
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        html = _report_html(result_json_path, monkeypatch)

        # Comportamento REALMENTE observado (confirmado, ver docstring
        # acima): nome do campo da resposta ("id") diferente do nome do
        # parâmetro de path ("customer_id") -> a linkagem não ocorre.
        assert file_names == ["test_get_customers_by_customer_id.py", "test_post_customers.py"]
        assert manifest["variable_dependencies"] == []
        assert not (suite_dir / "pytest.ini").exists()

        consumer_content = _endpoint_file(suite_dir, "get_customers")
        assert "@pytest.mark.skip" in consumer_content  # cai no fallback de sempre
        assert "_get_shared_variable" not in consumer_content
        assert "customer_id = 123" not in consumer_content  # nunca hardcoded, de qualquer forma

        # O servidor real só recebeu a chamada do produtor — o consumidor
        # nunca chegou a executar (skip), nunca uma chamada inválida.
        assert server.received_paths == ["/customers"]

        assert exit_code == SUCCESS
        assert payload["success"] is True  # nada FALHOU — só ficou "skipped"
        assert payload["summary"]["skipped"] == 1
        assert "<html" in html.lower() or "<!doctype" in html.lower()
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 1b — a mesma cadeia, mas com o campo de resposta nomeado
# "customer_id" (em vez de "id") — confirma o caminho feliz completo da
# tarefa (numeração, shared state, valor real, report) quando o nome da
# variável extraída bate exatamente com o nome do parâmetro de path.
# ============================================================================


def test_scenario1b_cli_full_pipeline_with_matching_field_name(tmp_path, monkeypatch):
    items = [
        {
            "name": "Create customer",
            "id": "post-customers",
            "request": {"method": "POST", "url": "https://api.exemplo.com/customers"},
            "response": [
                _json_example(201, "Created", {"customer_id": 123, "name": "Test Customer"})
            ],
        },
        {
            "name": "Get customer",
            "id": "get-customer",
            "request": {
                "method": "GET",
                "url": {
                    "raw": "https://api.exemplo.com/customers/{customer_id}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["customers", "{customer_id}"],
                    "variable": [],
                },
            },
            "response": [_json_example(200, "OK", {"status": "active"})],
        },
    ]
    collection_file = _write_collection(tmp_path / "collection", items)
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))
    assert file_names == [
        "01_test_post_customers.py",
        "02_test_get_customers_by_customer_id.py",
    ]

    producer_content = _endpoint_file(suite_dir, "01_test_post_customers")
    consumer_content = _endpoint_file(suite_dir, "02_test_get_customers_by_customer_id")
    assert "_set_shared_variable(" in producer_content
    assert "_get_shared_variable(" in consumer_content
    assert "customer_id = 123" not in consumer_content
    assert 'api_context.get("/customers/{customer_id}")' not in consumer_content
    assert 'f"/customers/{customer_id}"' in consumer_content

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["variable_dependencies"]) == 1
    assert manifest["variable_dependencies"][0]["variable"] == "customer_id"
    assert manifest["variable_dependencies"][0]["producer_test_id"] == "test_post_customers_success"

    server = PostmanTestServer()
    try:
        server.set_route(
            "/customers", method="POST", status=201, body={"customer_id": 123, "name": "Ada"}
        )
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        # 5 — chamadas HTTP reais, na ordem real: POST antes de GET.
        methods_in_order = server.received_methods
        assert methods_in_order.index("POST") < methods_in_order.index("GET")
        # 6/7 — valor produzido == valor consumido, confirmado pelo
        # próprio servidor (só respondeu 200 em /customers/123 porque o
        # GET chegou com esse path exato).
        assert "/customers/123" in server.received_paths
        assert exit_code == SUCCESS
        assert payload["success"] is True

        # 8/9 — test_id e assertions no result.json.
        assert "test_post_customers_success" in json.dumps(payload)
        assert "test_get_customers_by_customer_id_success" in json.dumps(payload)
        assert payload["summary"]["failed"] == 0

        html = _report_html(result_json_path, monkeypatch)
        # 12/13 — ReportEngine/HTML.
        assert "test_post_customers_success" in html
        assert "test_get_customers_by_customer_id_success" in html
        assert "200" in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 2 — falha do produtor
# ============================================================================


def test_scenario2_producer_failure_fails_consumer_explicitly(tmp_path, monkeypatch):
    items = [
        {
            "name": "Create customer",
            "id": "post-customers",
            "request": {"method": "POST", "url": "https://api.exemplo.com/customers"},
            "response": [
                _json_example(201, "Created", {"customer_id": 123, "name": "Test Customer"})
            ],
        },
        {
            "name": "Get customer",
            "id": "get-customer",
            "request": {
                "method": "GET",
                "url": {
                    "raw": "https://api.exemplo.com/customers/{customer_id}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["customers", "{customer_id}"],
                    "variable": [],
                },
            },
            "response": [_json_example(200, "OK", {"status": "active"})],
        },
    ]
    collection_file = _write_collection(tmp_path / "collection", items)
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    consumer_content = _endpoint_file(suite_dir, "02_test_get_customers_by_customer_id")
    assert "@pytest.mark.skip" not in consumer_content  # nunca skip — falha explícita

    server = PostmanTestServer()
    try:
        # Deliberadamente SEM rota para POST /customers -> 404 real.
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        assert payload["success"] is False
        failures = {f["test_name"]: f for f in payload["test_failures"]}
        producer_failure = next(name for name in failures if "post_customers" in name)
        consumer_failure_name = next(name for name in failures if "get_customers" in name)
        consumer_message = failures[consumer_failure_name]["error_message"]

        assert "customer_id" in consumer_message
        assert "indisponível" in consumer_message
        assert "test_post_customers_success" in consumer_message

        # GET nunca chamou o servidor com um valor vazio/placeholder.
        assert "/customers/123" not in server.received_paths
        assert "/customers/" not in server.received_paths
        assert "/customers/None" not in server.received_paths

        html = _report_html(result_json_path, monkeypatch)
        assert "status-failed" in html
        assert "indisponível" in html or "customer_id" in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 3 — múltiplos consumidores
# ============================================================================


def test_scenario3_multiple_consumers_share_the_same_produced_value(tmp_path, monkeypatch):
    def _url() -> dict:
        return {
            "raw": "https://api.exemplo.com/customers/{customer_id}",
            "protocol": "https",
            "host": ["api", "exemplo", "com"],
            "path": ["customers", "{customer_id}"],
            "variable": [],
        }

    items = [
        {
            "name": "Create customer",
            "id": "post-customers",
            "request": {"method": "POST", "url": "https://api.exemplo.com/customers"},
            "response": [_json_example(201, "Created", {"customer_id": 123})],
        },
        {
            "name": "Get customer",
            "id": "get-customer",
            "request": {"method": "GET", "url": _url()},
            "response": [_json_example(200, "OK", {"status": "active"})],
        },
        {
            "name": "Update customer",
            "id": "put-customer",
            "request": {"method": "PUT", "url": _url()},
            "response": [_json_example(200, "OK", {"updated": True})],
        },
        {
            "name": "Delete customer",
            "id": "delete-customer",
            "request": {"method": "DELETE", "url": _url()},
            # 204 nunca tem corpo (RFC 7231) — sem "Content-Type" no
            # exemplo, mesma convenção real de uma resposta vazia; usar
            # _json_example aqui declararia um Content-Type: application/
            # json incompatível com a resposta real (sem corpo) que o
            # servidor de teste devolve para 204.
            "response": [
                {"name": "No Content", "status": "No Content", "code": 204, "header": [], "body": ""}
            ],
        },
    ]
    collection_file = _write_collection(tmp_path / "collection", items)
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))
    assert file_names == [
        "01_test_post_customers.py",
        "02_test_get_customers_by_customer_id.py",
        "03_test_put_customers_by_customer_id.py",
        "04_test_delete_customers_by_customer_id.py",
    ]

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))
    dependencies = manifest["variable_dependencies"]
    assert len(dependencies) == 3  # GET, PUT, DELETE — todos consumindo o mesmo valor
    assert {entry["producer_test_id"] for entry in dependencies} == {
        "test_post_customers_success"
    }

    server = PostmanTestServer()
    try:
        server.set_route("/customers", method="POST", status=201, body={"customer_id": 123})
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})
        server.set_route("/customers/123", method="PUT", status=200, body={"updated": True})
        # P3.3: tests/postman_test_server.py ganhou do_DELETE (equivalente
        # a GET/POST/PUT) — DELETE agora é uma rota configurável de
        # verdade, nunca mais um 501 por limitação da infraestrutura de
        # teste (ver test_scenario3... em
        # test_playwright_variable_dependency_e2e_p3_3.py para a validação
        # completa desta correção).
        server.set_route("/customers/123", method="DELETE", status=204, body="")

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        post_count = server.received_methods.count("POST")
        assert post_count == 1  # produtor executa uma única vez

        get_paths = [
            p for m, p in zip(server.received_methods, server.received_paths) if m == "GET"
        ]
        put_paths = [
            p for m, p in zip(server.received_methods, server.received_paths) if m == "PUT"
        ]
        delete_paths = [
            p for m, p in zip(server.received_methods, server.received_paths) if m == "DELETE"
        ]
        assert "/customers/123" in get_paths
        assert "/customers/123" in put_paths
        assert "/customers/123" in delete_paths

        assert payload["success"] is True

        html = _report_html(result_json_path, monkeypatch)
        assert "test_post_customers_success" in html
        assert "test_get_customers_by_customer_id_success" in html
        assert "test_put_customers_by_customer_id_success" in html
        assert "test_delete_customers_by_customer_id_success" in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 4 — isolamento entre duas cadeias com o mesmo nome de variável
# ============================================================================


def test_scenario4_two_independent_chains_stay_isolated(tmp_path, monkeypatch):
    def _url(prefix: str) -> dict:
        return {
            "raw": f"https://api.exemplo.com/{prefix}/{{customer_id}}",
            "protocol": "https",
            "host": ["api", "exemplo", "com"],
            "path": [prefix, "{customer_id}"],
            "variable": [],
        }

    items = [
        {
            "name": "Create customer",
            "id": "post-customers",
            "request": {"method": "POST", "url": "https://api.exemplo.com/customers"},
            "response": [_json_example(201, "Created", {"customer_id": 123})],
        },
        {
            "name": "Get customer",
            "id": "get-customer",
            "request": {"method": "GET", "url": _url("customers")},
            "response": [_json_example(200, "OK", {"status": "active"})],
        },
        {
            "name": "Create order",
            "id": "post-orders",
            "request": {"method": "POST", "url": "https://api.exemplo.com/orders"},
            "response": [_json_example(201, "Created", {"customer_id": 456})],
        },
        {
            "name": "Get order",
            "id": "get-order",
            "request": {"method": "GET", "url": _url("orders")},
            "response": [_json_example(200, "OK", {"status": "placed"})],
        },
    ]
    collection_file = _write_collection(tmp_path / "collection", items)
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    server = PostmanTestServer()
    try:
        server.set_route("/customers", method="POST", status=201, body={"customer_id": 123})
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})
        server.set_route("/orders", method="POST", status=201, body={"customer_id": 456})
        server.set_route("/orders/456", method="GET", status=200, body={"status": "placed"})

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        assert payload["success"] is True
        assert "/customers/123" in server.received_paths
        assert "/orders/456" in server.received_paths
        # O valor 123 jamais aparece na cadeia B, e vice-versa.
        assert "/orders/123" not in server.received_paths
        assert "/customers/456" not in server.received_paths
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 5 — sem dependência (regressão de comportamento)
# ============================================================================


def test_scenario5_independent_endpoints_never_get_a_numeric_prefix(tmp_path, monkeypatch):
    items = [
        {
            "name": "Get products",
            "id": "get-products",
            "request": {"method": "GET", "url": "https://api.exemplo.com/products"},
            "response": [_json_example(200, "OK", {"items": []})],
        },
        {
            "name": "Get users",
            "id": "get-users",
            "request": {"method": "GET", "url": "https://api.exemplo.com/users"},
            "response": [_json_example(200, "OK", {"items": []})],
        },
    ]
    collection_file = _write_collection(tmp_path / "collection", items)
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))
    assert file_names == ["test_get_products.py", "test_get_users.py"]
    assert not (suite_dir / "pytest.ini").exists()

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))
    assert manifest["variable_dependencies"] == []

    server = PostmanTestServer()
    try:
        server.set_route("/products", method="GET", status=200, body={"items": []})
        server.set_route("/users", method="GET", status=200, body={"items": []})

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        assert exit_code == SUCCESS
        assert payload["success"] is True
    finally:
        server.shutdown()
