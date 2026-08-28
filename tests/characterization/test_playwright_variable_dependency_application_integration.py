"""P3.1 — Integração das dependências entre endpoints no fluxo REAL da
aplicação (nunca só infraestrutura/testes isolados).

Diferença deliberada em relação a test_playwright_variable_dependency_e2e.py
(que já prova a cadeia real via GeneratePlaywrightTestSuiteUseCase montado à
mão): este arquivo entra pelo PONTO REAL da aplicação — o mesmo `main()` da
CLI que um usuário invoca (`api_quality_agent generate --target
playwright`), composto pela MESMA `bootstrap.build_offline_context()` usada
em produção — nunca uma segunda composição de dependências, nunca chamando
`endpoint_dependency_linking.py` ou `PlaywrightEndpointTestGenerator`
diretamente.

Cadeia validada, ponta a ponta:

    arquivo .json (Collection) -> `main(["generate", ..., "--target",
    "playwright"])` -> bootstrap.build_offline_context()
    -> GeneratePlaywrightTestSuiteUseCase (linkagem + geracao, MESMO
    codigo do fluxo online) -> LocalArtifactRepository (arquivos .py
    fisicos reais em disco) -> Playwright real + pytest real + servidor
    HTTP real -> ExecutionResult

FRONTEIRA DOCUMENTADA (nunca o objeto sob validação): a EXECUÇÃO usa
`PlaywrightAdapter(pytest_executable=sys.executable,
command_prefix=("-m", "pytest"))` diretamente — a MESMA classe que
`bootstrap.build_offline_playwright_run_context()`/`run --target
playwright` compõe, invocada aqui só porque este ambiente de sandbox não
tem um executável `pytest` avulso no PATH (só `python -m pytest`); a CLI
`run` só aceita um único `--pytest-executable`, sem `command_prefix`. Mesmo
desvio documentado já usado por TODA a suíte de caracterização E2E deste
projeto (ver test_playwright_p2_pipeline_sanity.py). A GERAÇÃO (a parte
que este teste existe para provar) sempre passa pelo `main()` real.

Documenta o comportamento ATUAL — se quebrar por uma mudança deliberada,
atualize-o conscientemente (ver tests/characterization/README.md).
"""

import json
import sys
from pathlib import Path

from api_quality_agent.adapters.playwright import PlaywrightAdapter
from api_quality_agent.cli.exit_codes import SUCCESS
from api_quality_agent.cli.main import main
from postman_test_server import PostmanTestServer


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


def _write_chain_collection(path: Path) -> Path:
    # Mesma cadeia de test_playwright_variable_dependency_e2e.py (produtor
    # primeiro, dois consumidores do mesmo valor, um endpoint totalmente
    # independente por último) — desta vez como um ARQUIVO .json real em
    # disco, porque é assim que `generate --file` de fato recebe uma
    # Collection.
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
            # Sem campo "*_id" no corpo de resposta deste consumidor —
            # mesmo cuidado de test_playwright_variable_dependency_e2e.py
            # para não virar, incidentalmente, um segundo "produtor" de
            # customer_id.
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
    document = {
        "info": {
            "name": "Customer Chain",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / "collection.json"
    file_path.write_text(json.dumps(document), encoding="utf-8")
    return file_path


def _write_no_dependency_collection(path: Path) -> Path:
    document = {
        "info": {
            "name": "Simple",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            {
                "name": "Get pets",
                "id": "get-pets",
                "request": {"method": "GET", "url": "https://api.exemplo.com/pets"},
                "response": [_json_example(200, "OK", {"items": []})],
            },
            {
                "name": "Create pet",
                "id": "post-pets",
                "request": {"method": "POST", "url": "https://api.exemplo.com/pets"},
                "response": [_json_example(201, "Created", {"name": "Rex"})],
            },
        ],
    }
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / "collection.json"
    file_path.write_text(json.dumps(document), encoding="utf-8")
    return file_path


def _generate_playwright_via_cli(tmp_path: Path, monkeypatch, collection_file: Path) -> Path:
    # O PONTO REAL da aplicação: o mesmo `main()` que um usuário roda no
    # terminal, com a mesma composição de dependências
    # (bootstrap.build_offline_context()) usada em produção — nenhuma
    # segunda composição criada só para este teste.
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    exit_code = main(
        ["generate", "--file", str(collection_file), "-y", "--target", "playwright"]
    )
    assert exit_code == SUCCESS

    suite_dirs = list(tmp_path.glob("artifacts/local/*/*/scripts/playwright"))
    assert len(suite_dirs) == 1, f"suíte não encontrada em artifacts/: {suite_dirs}"
    return suite_dirs[0]


def _run_real(suite_dir: Path, base_url: str, monkeypatch, *, timeout: float = 120.0):
    # Mesma classe PlaywrightAdapter composta por
    # bootstrap.build_offline_playwright_run_context() (`run --target
    # playwright`) — ver desvio documentado no topo do arquivo.
    monkeypatch.setenv("PLAYWRIGHT_BASE_URL", base_url)
    adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
    return adapter.run(tests_path=str(suite_dir), timeout_seconds=timeout)


# ============================================================================
# Critérios de aceite 1-4: a aplicação (via CLI real) executa a linkagem,
# variable_usages chega à geração, a ordem topológica é usada, e os
# arquivos dependentes recebem 01_/02_/03_ (independentes não recebem).
# ============================================================================


def test_cli_generate_playwright_numbers_files_only_when_a_dependency_exists(
    tmp_path, monkeypatch
):
    chain_file = _write_chain_collection(tmp_path / "chain")
    suite_dir = _generate_playwright_via_cli(tmp_path / "chain_run", monkeypatch, chain_file)

    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))
    assert file_names == [
        "01_test_post_customers.py",
        "02_test_get_customers_by_customer_id.py",
        "03_test_put_customers_by_customer_id.py",
        "04_test_get_products.py",
    ]

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.3"
    dependencies = manifest["variable_dependencies"]
    assert len(dependencies) == 2  # GET e PUT, ambos consumindo o mesmo valor
    assert {entry["variable"] for entry in dependencies} == {"customer_id"}
    assert {entry["producer_test_id"] for entry in dependencies} == {
        "test_post_customers_success"
    }


def test_cli_generate_playwright_never_adds_a_prefix_without_a_dependency(tmp_path, monkeypatch):
    no_dep_file = _write_no_dependency_collection(tmp_path / "no_dep")
    suite_dir = _generate_playwright_via_cli(tmp_path / "no_dep_run", monkeypatch, no_dep_file)

    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))
    assert file_names == ["test_get_pets.py", "test_post_pets.py"]

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))
    assert manifest["variable_dependencies"] == []
    assert not (suite_dir / "pytest.ini").exists()


# ============================================================================
# Critérios de aceite 5-9: shared state propagado pela aplicação, execução
# real produtor -> consumidor, falha explícita, múltiplos consumidores,
# isolamento entre cadeias independentes.
# ============================================================================


def test_cli_generated_chain_executes_for_real_producer_to_multiple_consumers(
    tmp_path, monkeypatch
):
    chain_file = _write_chain_collection(tmp_path / "chain")
    suite_dir = _generate_playwright_via_cli(tmp_path / "chain_run", monkeypatch, chain_file)

    server = PostmanTestServer()
    try:
        server.set_route(
            "/customers", method="POST", status=201, body={"customer_id": 123, "name": "Ada"}
        )
        server.set_route(
            "/customers/123", method="GET", status=200, body={"status": "active"}
        )
        server.set_route("/customers/123", method="PUT", status=200, body={"updated": True})
        server.set_route("/products", method="GET", status=200, body={"items": []})

        result = _run_real(suite_dir, server.base_url, monkeypatch)

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-3000:]} {result.stderr[-3000:]}"
        )
        assert result.success is True

        # Prova real (nunca mockada) de que o valor produzido pelo POST
        # (via generate --target playwright real) chegou aos DOIS
        # consumidores, sem reexecutar o produtor: GET e PUT chamaram
        # exatamente /customers/123.
        get_transaction = next(
            t for t in result.http_transactions if t.method == "GET" and "customers" in t.url
        )
        assert get_transaction.url.endswith("/customers/123")
        put_transaction = next(t for t in result.http_transactions if t.method == "PUT")
        assert put_transaction.url.endswith("/customers/123")

        # Endpoint independente (Etapa 9) segue funcionando normalmente.
        products_transaction = next(
            t for t in result.http_transactions if t.url.endswith("/products")
        )
        assert products_transaction.response_status == 200
    finally:
        server.shutdown()


def test_cli_generated_chain_producer_failure_fails_consumer_explicitly(tmp_path, monkeypatch):
    chain_file = _write_chain_collection(tmp_path / "chain")
    suite_dir = _generate_playwright_via_cli(tmp_path / "chain_run", monkeypatch, chain_file)

    server = PostmanTestServer()
    try:
        # Deliberadamente SEM rota para POST /customers -> 404 real -> o
        # produtor falha e a extração também (corpo do 404 não tem "id")
        # -> nada é armazenado no shared state.
        server.set_route("/customers/123", method="GET", status=200, body={"status": "active"})
        server.set_route("/customers/123", method="PUT", status=200, body={"updated": True})
        server.set_route("/products", method="GET", status=200, body={"items": []})

        result = _run_real(suite_dir, server.base_url, monkeypatch)

        assert result.infrastructure_failure is None
        assert result.success is False

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
    finally:
        server.shutdown()


def _write_isolation_collection(path: Path) -> Path:
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
            "response": [_json_example(201, "Created", {"customer_id": 111})],
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
            "response": [_json_example(201, "Created", {"customer_id": 222})],
        },
        {
            "name": "Get order",
            "id": "get-order",
            "request": {"method": "GET", "url": _url("orders")},
            "response": [_json_example(200, "OK", {"status": "placed"})],
        },
    ]
    document = {
        "info": {
            "name": "Isolation",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / "collection.json"
    file_path.write_text(json.dumps(document), encoding="utf-8")
    return file_path


def test_cli_two_independent_chains_sharing_a_variable_name_stay_isolated(tmp_path, monkeypatch):
    # Etapa 9: "POST /customers -> customer_id -> GET /customers/{customer_id}"
    # e "POST /orders -> customer_id -> GET /orders/{customer_id}" usam o
    # MESMO nome de variável — a correlação (producer_test_id,
    # variable_name) precisa impedir qualquer vazamento entre as duas.
    isolation_file = _write_isolation_collection(tmp_path / "isolation")
    suite_dir = _generate_playwright_via_cli(
        tmp_path / "isolation_run", monkeypatch, isolation_file
    )

    server = PostmanTestServer()
    try:
        server.set_route("/customers", method="POST", status=201, body={"customer_id": 111})
        server.set_route("/customers/111", method="GET", status=200, body={"status": "active"})
        server.set_route("/orders", method="POST", status=201, body={"customer_id": 222})
        server.set_route("/orders/222", method="GET", status=200, body={"status": "placed"})

        result = _run_real(suite_dir, server.base_url, monkeypatch)

        assert result.infrastructure_failure is None
        assert result.success is True, f"cadeias deveriam passar isoladamente: {result.stdout[-3000:]}"

        get_customer = next(
            t for t in result.http_transactions if t.method == "GET" and "customers" in t.url
        )
        get_order = next(
            t for t in result.http_transactions if t.method == "GET" and "orders" in t.url
        )
        assert get_customer.url.endswith("/customers/111")
        assert get_order.url.endswith("/orders/222")
    finally:
        server.shutdown()


# ============================================================================
# Critério de aceite 10 — ciclo, através da aplicação real
# ============================================================================


def _write_cycle_collection(path: Path) -> Path:
    items = [
        {
            "name": "A",
            "id": "req-a",
            "request": {
                "method": "POST",
                "url": {
                    "raw": "https://api.exemplo.com/a/{a_id}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["a", "{a_id}"],
                    "variable": [],
                },
            },
            "response": [_json_example(201, "Created", {"b_id": 1})],
        },
        {
            "name": "B",
            "id": "req-b",
            "request": {
                "method": "POST",
                "url": {
                    "raw": "https://api.exemplo.com/b/{b_id}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["b", "{b_id}"],
                    "variable": [],
                },
            },
            "response": [_json_example(201, "Created", {"a_id": 2})],
        },
    ]
    document = {
        "info": {
            "name": "Cycle",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / "collection.json"
    file_path.write_text(json.dumps(document), encoding="utf-8")
    return file_path


def test_cli_generate_playwright_detects_a_cycle_and_never_picks_an_arbitrary_order(
    tmp_path, monkeypatch
):
    cycle_file = _write_cycle_collection(tmp_path / "cycle")
    suite_dir = _generate_playwright_via_cli(tmp_path / "cycle_run", monkeypatch, cycle_file)

    manifest = json.loads((suite_dir / "generation-manifest.json").read_text(encoding="utf-8"))

    # Nenhum VariableUsage sobrevive a um ciclo — nunca uma cadeia
    # incorreta, mesmo com A e B mutuamente dependentes.
    assert manifest["variable_dependencies"] == []

    warning_codes = {entry["code"] for entry in manifest["warnings"]}
    assert "CIRCULAR_VARIABLE_DEPENDENCY" in warning_codes

    # Sem dependência realmente ligada, nenhum prefixo numérico é aplicado
    # (mesma regra de "não adicionar prefixo desnecessariamente").
    file_names = sorted(p.name for p in (suite_dir / "endpoints").glob("*.py"))
    assert all(not name[:2].isdigit() for name in file_names)
