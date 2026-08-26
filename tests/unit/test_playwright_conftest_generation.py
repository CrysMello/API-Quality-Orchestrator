"""Parte 08 do plano de ação Playwright: geração real de conftest.py, com
fixture `api_context` (APIRequestContext, sem browser/page), base_url
configurável e dispose() garantido no teardown.

Nunca usa o pacote `playwright` real (que por acaso está instalado nesta
máquina para outros fins) — só um fake mínimo em
tests/fake_playwright_package/, no mesmo espírito de tests/fake_newman.py:
provar que o conteúdo gerado é importável/coletável e se comporta como
esperado, sem depender de infraestrutura externa nem de qual pacote
acontece de estar instalado.
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from api_quality_agent.domain.models import ExecutionContext, ExecutionMode
from api_quality_agent.generators.playwright import (
    DefaultPlaywrightTestSuiteBuilder,
    GeneratedEndpointTest,
)

_FAKE_PLAYWRIGHT_ROOT = Path(__file__).resolve().parent.parent / "fake_playwright_package"


def _endpoint_test(**overrides) -> GeneratedEndpointTest:
    defaults = {
        "endpoint_source": "GET /users",
        "suggested_file_name": "test_get_users.py",
        "content": "def test_get_users_success(api_context):\n    ...\n",
        "scenario_names": ("success",),
        "warnings": (),
        "base_url": "https://api.exemplo.com",
    }
    defaults.update(overrides)
    return GeneratedEndpointTest(**defaults)


def _context() -> ExecutionContext:
    return ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="Col")


def _conftest_content(endpoint_tests=None) -> str:
    suite = DefaultPlaywrightTestSuiteBuilder().build(
        list(endpoint_tests) if endpoint_tests is not None else [_endpoint_test()], _context()
    )
    return next(f.content for f in suite.files if f.relative_path == "conftest.py")


# --- Conteúdo estático ---------------------------------------------------


def test_conftest_defines_the_api_context_fixture():
    content = _conftest_content()

    assert "@pytest.fixture" in content
    assert "def api_context(" in content


def test_conftest_uses_api_request_context_and_sync_playwright():
    content = _conftest_content()

    assert "from playwright.sync_api import APIRequestContext, sync_playwright" in content
    assert "sync_playwright()" in content
    assert "playwright.request.new_context(" in content


def test_conftest_never_uses_browser_or_page():
    # Checa uso real de API de browser/page, não a palavra em si — o
    # próprio docstring do arquivo gerado documenta "sem browser/page".
    content = _conftest_content().lower()

    for forbidden_api in (
        "chromium",
        "firefox",
        "webkit",
        ".launch(",
        ".new_page(",
        "playwright.chromium",
    ):
        assert forbidden_api not in content


def test_conftest_calls_dispose_inside_a_finally_block():
    content = _conftest_content()
    tree = ast.parse(content)

    fixture_function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "api_context"
    )
    try_nodes = [node for node in ast.walk(fixture_function) if isinstance(node, ast.Try)]
    assert try_nodes, "esperava um bloco try/finally dentro da fixture"

    dispose_in_finally = any(
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute)
        and stmt.value.func.attr == "dispose"
        for try_node in try_nodes
        for stmt in try_node.finalbody
    )
    assert dispose_in_finally


def test_conftest_base_url_is_configurable_via_environment_variable():
    content = _conftest_content()

    assert "PLAYWRIGHT_BASE_URL" in content
    assert "os.environ.get(_BASE_URL_ENV_VAR, _DEFAULT_BASE_URL)" in content


def test_conftest_default_base_url_is_derived_from_the_first_endpoint():
    content = _conftest_content(
        [_endpoint_test(base_url="https://api.exemplo.com"), _endpoint_test(base_url=None)]
    )

    assert '_DEFAULT_BASE_URL = "https://api.exemplo.com"' in content


def test_conftest_default_base_url_is_empty_when_not_determinable():
    content = _conftest_content([_endpoint_test(base_url=None)])

    assert '_DEFAULT_BASE_URL = ""' in content


def test_conftest_content_never_depends_on_endpoint_specific_data():
    # conftest.py é compartilhado — não deve variar por causa de warnings,
    # cenários ou conteúdo específico de um endpoint, só pelo base_url.
    content_a = _conftest_content(
        [_endpoint_test(endpoint_source="GET /a", content="A", warnings=())]
    )
    content_b = _conftest_content(
        [_endpoint_test(endpoint_source="GET /b", content="B", warnings=())]
    )

    assert content_a == content_b


def test_conftest_content_is_syntactically_valid_python():
    ast.parse(_conftest_content())


# --- Coleta real pelo pytest (com o fake, nunca o playwright real) -------


def test_conftest_is_collected_by_a_real_pytest_process(tmp_path):
    scripts_dir = tmp_path / "scripts" / "playwright"
    (scripts_dir / "endpoints").mkdir(parents=True)
    (scripts_dir / "conftest.py").write_text(_conftest_content(), encoding="utf-8")
    (scripts_dir / "endpoints" / "test_get_users.py").write_text(
        "def test_get_users_success(api_context):\n"
        "    response = api_context.get('/users')\n"
        "    assert response is not None\n",
        encoding="utf-8",
    )

    env = {**os.environ, "PYTHONPATH": str(_FAKE_PLAYWRIGHT_ROOT)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(scripts_dir)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_get_users_success" in result.stdout


# --- dispose() realmente acontece (com o fake, driblando a proteção do -----
# --- pytest contra chamar fixtures diretamente) ---------------------------


@pytest.fixture
def fake_playwright(monkeypatch):
    monkeypatch.syspath_prepend(str(_FAKE_PLAYWRIGHT_ROOT))
    for name in list(sys.modules):
        if name == "playwright" or name.startswith("playwright."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    from playwright.sync_api import reset_state  # type: ignore[import-not-found]

    reset_state()
    import playwright.sync_api as fake_sync_api  # type: ignore[import-not-found]

    return fake_sync_api


def test_api_context_fixture_yields_a_context_and_disposes_it(tmp_path, fake_playwright):
    module_path = tmp_path / "generated_conftest.py"
    module_path.write_text(_conftest_content(), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("generated_conftest", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generator = module.api_context.__wrapped__()
    context = next(generator)

    assert isinstance(context, fake_playwright.APIRequestContext)
    assert context.disposed is False
    assert context.base_url == "https://api.exemplo.com"

    with pytest.raises(StopIteration):
        next(generator)

    assert context.disposed is True
    assert context in fake_playwright.DISPOSED_CONTEXTS


def test_api_context_disposes_even_when_the_test_raises(tmp_path, fake_playwright):
    module_path = tmp_path / "generated_conftest.py"
    module_path.write_text(_conftest_content(), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("generated_conftest_2", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generator = module.api_context.__wrapped__()
    context = next(generator)

    # Simula o pytest propagando uma falha de teste para dentro do gerador
    # (throw), como acontece quando o teste que usa a fixture falha.
    with pytest.raises(RuntimeError):
        generator.throw(RuntimeError("falha simulada do teste"))

    assert context.disposed is True


# --- P1.2: captura de transação HTTP (contra o fake, nunca o playwright real) --------------


def _load_wrapped_context(tmp_path: Path, module_name: str):
    module_path = tmp_path / f"{module_name}.py"
    module_path.write_text(_conftest_content(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generator = module.api_context.__wrapped__()
    return generator, next(generator)


def _read_transactions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_wrapped_context_records_a_get_call(tmp_path, fake_playwright, monkeypatch):
    transactions_path = tmp_path / "transactions.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_HTTP_TRANSACTIONS_PATH", str(transactions_path))
    generator, context = _load_wrapped_context(tmp_path, "generated_conftest_get")

    response = context.get("/users", headers={"Accept": "application/json"})

    assert response.status == 200  # a chamada continua funcionando pra quem a fez
    transactions = _read_transactions(transactions_path)
    assert len(transactions) == 1
    assert transactions[0]["method"] == "GET"
    assert transactions[0]["request_headers"] == {"Accept": "application/json"}
    with pytest.raises(StopIteration):
        next(generator)


def test_wrapped_context_records_a_post_call_with_body(tmp_path, fake_playwright, monkeypatch):
    transactions_path = tmp_path / "transactions.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_HTTP_TRANSACTIONS_PATH", str(transactions_path))
    fake_playwright.NEXT_RESPONSE.update(status=201, body='{"id": 1}')
    generator, context = _load_wrapped_context(tmp_path, "generated_conftest_post")

    context.post("/users", data={"name": "Maria", "active": True})

    transactions = _read_transactions(transactions_path)
    assert transactions[0]["method"] == "POST"
    assert transactions[0]["request_body"] == {"name": "Maria", "active": True}


def test_wrapped_context_records_the_response(tmp_path, fake_playwright, monkeypatch):
    transactions_path = tmp_path / "transactions.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_HTTP_TRANSACTIONS_PATH", str(transactions_path))
    fake_playwright.NEXT_RESPONSE.update(
        status=404, headers={"content-type": "application/json"}, body='{"error": "not found"}'
    )
    generator, context = _load_wrapped_context(tmp_path, "generated_conftest_response")

    context.get("/users/999")

    transactions = _read_transactions(transactions_path)
    assert transactions[0]["response_status"] == 404
    assert transactions[0]["response_body"] == '{"error": "not found"}'
    assert transactions[0]["response_headers"] == {"content-type": "application/json"}


def test_wrapped_context_records_absence_of_request_body(tmp_path, fake_playwright, monkeypatch):
    transactions_path = tmp_path / "transactions.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_HTTP_TRANSACTIONS_PATH", str(transactions_path))
    generator, context = _load_wrapped_context(tmp_path, "generated_conftest_no_body")

    context.delete("/users/1")

    transactions = _read_transactions(transactions_path)
    assert transactions[0]["method"] == "DELETE"
    assert transactions[0]["request_body"] is None


def test_wrapped_context_records_multiple_calls(tmp_path, fake_playwright, monkeypatch):
    transactions_path = tmp_path / "transactions.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_HTTP_TRANSACTIONS_PATH", str(transactions_path))
    generator, context = _load_wrapped_context(tmp_path, "generated_conftest_multiple")

    context.get("/users")
    context.post("/users", data={"name": "Maria"})
    context.delete("/users/1")

    transactions = _read_transactions(transactions_path)
    assert [t["method"] for t in transactions] == ["GET", "POST", "DELETE"]


def test_wrapped_context_uses_method_kwarg_for_fetch(tmp_path, fake_playwright, monkeypatch):
    transactions_path = tmp_path / "transactions.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_HTTP_TRANSACTIONS_PATH", str(transactions_path))
    generator, context = _load_wrapped_context(tmp_path, "generated_conftest_fetch")

    context.fetch("/users", method="OPTIONS")

    transactions = _read_transactions(transactions_path)
    assert transactions[0]["method"] == "OPTIONS"


def test_capture_is_a_no_op_without_the_env_var(tmp_path, fake_playwright, monkeypatch):
    # Ausência de PLAYWRIGHT_HTTP_TRANSACTIONS_PATH (ex.: pytest rodado
    # direto, fora do PlaywrightAdapter) nunca gera erro nem arquivo.
    monkeypatch.delenv("PLAYWRIGHT_HTTP_TRANSACTIONS_PATH", raising=False)
    transactions_path = tmp_path / "nunca-deveria-existir.ndjson"
    generator, context = _load_wrapped_context(tmp_path, "generated_conftest_no_env")

    response = context.get("/users")

    assert response.status == 200
    assert not transactions_path.exists()
