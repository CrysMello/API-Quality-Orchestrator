"""P3.3 — E2E real, via CLI, para a correção da resolução semântica de
variável extraída (json_path != variable_name).

Cenário-motivador (achado do P3.2): um campo de resposta genérico ("id")
nunca linkava com um parâmetro de path com nome de negócio
("customer_id"), mesmo quando a Collection já dizia explicitamente, por
um script de teste real (`pm.collectionVariables.set("customer_id",
pm.response.json().id)`), que o valor produzido SE CHAMA "customer_id".
Este arquivo prova, com CLI real (generate -> run -> report), Playwright
real, servidor HTTP real e o mecanismo de script REAL da própria
Collection (nunca um matching heurístico de nomes), que:

    response = {"id": 123}
    script de teste: pm.collectionVariables.set("customer_id", ...id)
    -> VariableExtraction(variable_name="customer_id", json_path="$.id")
    -> VariableUsage(variable_name="customer_id") no consumidor
    -> 01_test_post_customers.py / 02_.../03_.../04_...
    -> GET/PUT/DELETE /customers/123 real

Mesmo padrão de infraestrutura de
test_playwright_variable_dependency_cli_full_pipeline_p3_2.py (main() real
da CLI para generate/run/report; PostmanTestServer real, agora com
do_DELETE real — ver P3.3 seção 7). Nenhum Generator/UseCase/Adapter/
Builder/runtime state mockado.

Documenta o comportamento ATUAL — se quebrar por uma mudança deliberada,
atualize-o conscientemente (ver tests/characterization/README.md).
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


def _customer_id_test_script() -> list:
    # Script de teste REAL da Collection (nunca um sintético paralelo) —
    # mesmo formato já usado em Collections Postman reais para capturar o
    # id de um recurso recém-criado numa variável nomeada.
    return [
        {
            "listen": "test",
            "script": {
                "exec": [
                    "const data = pm.response.json();",
                    'pm.collectionVariables.set("customer_id", data.id);',
                ]
            },
        }
    ]


def _json_example(code: int, status: str, body: dict) -> dict:
    return {
        "name": status,
        "status": status,
        "code": code,
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": json.dumps(body),
    }


def _no_content_example() -> dict:
    return {"name": "No Content", "status": "No Content", "code": 204, "header": [], "body": ""}


def _customer_path_url() -> dict:
    return {
        "raw": "https://api.exemplo.com/customers/{customer_id}",
        "protocol": "https",
        "host": ["api", "exemplo", "com"],
        "path": ["customers", "{customer_id}"],
        "variable": [],
    }


def _write_collection(path: Path, items: list) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    document = {
        "info": {
            "name": "P3.3",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }
    file_path = path / "collection.json"
    file_path.write_text(json.dumps(document), encoding="utf-8")
    return file_path


def _generate_playwright(tmp_path: Path, monkeypatch, collection_file: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    exit_code = main(["generate", "--file", str(collection_file), "-y", "--target", "playwright"])
    assert exit_code == SUCCESS

    suite_dirs = list(tmp_path.glob("artifacts/local/*/*/scripts/playwright"))
    assert len(suite_dirs) == 1, f"suíte não encontrada em artifacts/: {suite_dirs}"
    return suite_dirs[0]


def _run_playwright(suite_dir: Path, base_url: str, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PLAYWRIGHT_BASE_URL", base_url)

    if _REAL_PYTEST_EXECUTABLE is not None:
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
        # DESVIO DOCUMENTADO (ver test_playwright_variable_dependency_cli_
        # full_pipeline_p3_2.py para a justificativa completa):
        # --pytest-executable da CLI não aceita "-m pytest"; sem um
        # console-script pytest utilizável neste ambiente, usa
        # PlaywrightAdapter direto (mesma classe da CLI) + mesma
        # persistência real.
        from datetime import datetime

        from api_quality_agent.adapters.filesystem import JsonExecutionResultRepository
        from api_quality_agent.adapters.playwright import PlaywrightAdapter
        from api_quality_agent.application.use_cases import PersistExecutionResultUseCase

        adapter = PlaywrightAdapter(
            pytest_executable=sys.executable, command_prefix=("-m", "pytest")
        )
        started_at = datetime.now()
        result = adapter.run(tests_path=str(suite_dir), timeout_seconds=120.0)
        finished_at = datetime.now()
        PersistExecutionResultUseCase(JsonExecutionResultRepository()).execute(
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


def _report_html(result_json_path: Path) -> str:
    exit_code = main(["report", "--input", str(result_json_path), "--overwrite"])
    assert exit_code == SUCCESS
    html_path = result_json_path.parent / "report.html"
    assert html_path.is_file()
    return html_path.read_text(encoding="utf-8")


def _endpoint_file(suite_dir: Path, name_contains: str) -> str:
    matches = [p for p in (suite_dir / "endpoints").glob("*.py") if name_contains in p.name]
    assert len(matches) == 1, f"esperado 1 arquivo contendo {name_contains!r}, achei {matches}"
    return matches[0].read_text(encoding="utf-8")


def _chain_items() -> list:
    return [
        {
            "name": "Create customer",
            "id": "post-customers",
            "request": {"method": "POST", "url": "https://api.exemplo.com/customers"},
            "event": _customer_id_test_script(),
            "response": [_json_example(201, "Created", {"id": 123, "name": "Customer"})],
        },
        {
            "name": "Get customer",
            "id": "get-customer",
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
            "name": "Delete customer",
            "id": "delete-customer",
            "request": {"method": "DELETE", "url": _customer_path_url()},
            "response": [_no_content_example()],
        },
    ]


# ============================================================================
# Seção 9 — E2E real da correção: id -> customer_id, cadeia completa,
# múltiplos consumidores (GET/PUT/DELETE), POST executa uma única vez.
# ============================================================================


def test_id_field_with_explicit_semantic_name_links_and_executes_for_real(tmp_path, monkeypatch):
    collection_file = _write_collection(tmp_path / "collection", _chain_items())
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    # 1/2/3 — arquivos físicos, conteúdo, ordem: a correção fez a
    # linkagem acontecer (antes do P3.3, isto ficava sem prefixo nenhum).
    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))
    assert file_names == [
        "01_test_post_customers.py",
        "02_test_get_customers_by_customer_id.py",
        "03_test_put_customers_by_customer_id.py",
        "04_test_delete_customers_by_customer_id.py",
    ]

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))
    dependencies = manifest["variable_dependencies"]
    assert len(dependencies) == 3  # GET, PUT, DELETE — todos consumindo customer_id
    assert {entry["variable"] for entry in dependencies} == {"customer_id"}
    assert {entry["producer_test_id"] for entry in dependencies} == {
        "test_post_customers_success"
    }

    producer_content = _endpoint_file(suite_dir, "01_test_post_customers")
    assert 'customer_id = _get_nested_value(body, ("id",))' in producer_content
    assert (
        '_set_shared_variable("test_post_customers_success", "customer_id", customer_id)'
        in producer_content
    )

    for consumer_file_fragment in ("get_customers", "put_customers", "delete_customers"):
        content = _endpoint_file(suite_dir, consumer_file_fragment)
        assert (
            'customer_id = _get_shared_variable("test_post_customers_success", "customer_id")'
            in content
        )
        assert 'f"/customers/{customer_id}"' in content
        # Forma proibida: o placeholder cru como literal (sem "f" na
        # frente) — nunca confundir com a f-string acima, que CONTÉM essa
        # mesma substring (armadilha já corrigida em P2.5).
        assert '"/customers/{customer_id}")' not in content.replace('f"/customers/{customer_id}")', "")
        assert "customer_id = 123" not in content  # nunca hardcoded
        assert "AQO_CUSTOMER_ID" not in content  # nunca via variável de ambiente

    server = PostmanTestServer()
    try:
        server.set_route(
            "/customers", method="POST", status=201, body={"id": 123, "name": "Customer"}
        )
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})
        server.set_route("/customers/123", method="PUT", status=200, body={"updated": True})
        server.set_route("/customers/123", method="DELETE", status=204, body="")

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        assert exit_code == SUCCESS
        assert payload["success"] is True

        # 4/5/6/7 — shared state, chamadas HTTP reais, valor produzido ==
        # valor consumido pelos três, POST executa uma única vez.
        assert server.received_methods.count("POST") == 1
        for method in ("GET", "PUT", "DELETE"):
            paths = [
                p for m, p in zip(server.received_methods, server.received_paths) if m == method
            ]
            assert paths == ["/customers/123"], f"{method} não recebeu o valor produzido"

        # 8/9 — test_id e assertions no result.json.
        assert "test_post_customers_success" in json.dumps(payload)
        assert "test_get_customers_by_customer_id_success" in json.dumps(payload)
        assert "test_put_customers_by_customer_id_success" in json.dumps(payload)
        assert "test_delete_customers_by_customer_id_success" in json.dumps(payload)
        assert payload["summary"]["failed"] == 0

        # 12/13 — ReportEngine/HTML.
        html = _report_html(result_json_path)
        for test_id in (
            "test_post_customers_success",
            "test_get_customers_by_customer_id_success",
            "test_put_customers_by_customer_id_success",
            "test_delete_customers_by_customer_id_success",
        ):
            assert test_id in html
        assert "status-passed" in html
    finally:
        server.shutdown()


# ============================================================================
# Seção 10 — falha do produtor, com a variável semântica (customer_id)
# ============================================================================


def test_producer_failure_fails_consumer_explicitly_with_semantic_variable_name(
    tmp_path, monkeypatch
):
    items = _chain_items()[:2]  # só POST + GET, suficiente para este cenário
    collection_file = _write_collection(tmp_path / "collection", items)
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    consumer_content = _endpoint_file(suite_dir, "get_customers")
    assert "@pytest.mark.skip" not in consumer_content  # nunca vira skip

    server = PostmanTestServer()
    try:
        # Deliberadamente SEM rota para POST /customers -> 404 real.
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        assert payload["success"] is False
        failures = {f["test_name"]: f["error_message"] for f in payload["test_failures"]}
        producer_name = next(name for name in failures if "post_customers" in name)
        consumer_name = next(name for name in failures if "get_customers" in name)
        assert producer_name  # produtor realmente falhou

        consumer_message = failures[consumer_name]
        assert "customer_id" in consumer_message
        assert "indisponível" in consumer_message
        assert "test_post_customers_success" in consumer_message

        # Nunca uma chamada HTTP inválida.
        assert "/customers/123" not in server.received_paths
        assert "/customers/" not in server.received_paths
        assert "/customers/None" not in server.received_paths

        html = _report_html(result_json_path)
        assert "status-failed" in html
    finally:
        server.shutdown()


# ============================================================================
# Seção 11 — isolamento entre duas cadeias, ambas produzindo "customer_id"
# a partir de um campo genérico "id" no MESMO padrão (id -> customer_id)
# ============================================================================


def _isolation_items() -> list:
    def _url(prefix: str) -> dict:
        return {
            "raw": f"https://api.exemplo.com/{prefix}/{{customer_id}}",
            "protocol": "https",
            "host": ["api", "exemplo", "com"],
            "path": [prefix, "{customer_id}"],
            "variable": [],
        }

    return [
        {
            "name": "Create customer",
            "id": "post-customers",
            "request": {"method": "POST", "url": "https://api.exemplo.com/customers"},
            "event": _customer_id_test_script(),
            "response": [_json_example(201, "Created", {"id": 123})],
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
            "event": _customer_id_test_script(),
            "response": [_json_example(201, "Created", {"id": 456})],
        },
        {
            "name": "Get order",
            "id": "get-order",
            "request": {"method": "GET", "url": _url("orders")},
            "response": [_json_example(200, "OK", {"status": "placed"})],
        },
    ]


def test_two_chains_producing_the_same_semantic_name_from_a_generic_id_field_stay_isolated(
    tmp_path, monkeypatch
):
    collection_file = _write_collection(tmp_path / "collection", _isolation_items())
    suite_dir = _generate_playwright(tmp_path / "run", monkeypatch, collection_file)

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))
    dependencies = manifest["variable_dependencies"]
    assert len(dependencies) == 2
    assert {entry["variable"] for entry in dependencies} == {"customer_id"}
    producer_ids = {entry["producer_test_id"] for entry in dependencies}
    assert producer_ids == {"test_post_customers_success", "test_post_orders_success"}

    server = PostmanTestServer()
    try:
        server.set_route("/customers", method="POST", status=201, body={"id": 123})
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})
        server.set_route("/orders", method="POST", status=201, body={"id": 456})
        server.set_route("/orders/456", method="GET", status=200, body={"status": "placed"})

        exit_code, payload, result_json_path = _run_playwright(
            suite_dir, server.base_url, monkeypatch, tmp_path / "run"
        )

        assert payload["success"] is True, "cadeias deveriam passar isoladamente"

        assert "/customers/123" in server.received_paths
        assert "/orders/456" in server.received_paths
        # O valor 123 jamais aparece na cadeia de orders, e vice-versa.
        assert "/orders/123" not in server.received_paths
        assert "/customers/456" not in server.received_paths
    finally:
        server.shutdown()
