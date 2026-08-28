"""P2.5 — Regressão E2E completa do mecanismo de proteção de secrets do
fluxo Playwright (correção do P2.4:
`_find_matching_secret_variable_name` em playwright_endpoint_test_
generator.py).

Este arquivo NÃO duplica os cenários já cobertos permanentemente por:
  - tests/characterization/test_playwright_literal_secret_e2e.py
    (Cenário A: {{variável}} secreta; Cenário B: literal de BODY
    correspondente a secret; Cenário E: literal de QUERY correspondente a
    secret; Cenário F: literal de HEADER correspondente a secret — todos
    com execução E2E real, servidor real, result.json e HTML).
  - tests/characterization/test_playwright_generated_source_secret_
    exposure.py (Authorization sem Environment; literal sem nenhuma
    correspondência).

Cobre exclusivamente o que ainda não tinha um teste permanente dedicado:

  C — literal comum (não secreto) permanece intocado;
  D — secret embutido como SUBSTRING de um literal maior NÃO é detectado
      (limitação documentada e deliberadamente preservada — correspondência
      é sempre por valor EXATO, nunca substring);
  G — Authorization com um valor que TAMBÉM bate com um secret do
      Environment continua sendo omitido pela regra especial já existente
      (RESERVED_HEADER_NAMES/SENSITIVE_HEADER_OMITTED), nunca deferido
      pelo novo mecanismo do P2.4;
  H — múltiplos secrets na MESMA request (a correção não pode proteger só
      o primeiro encontrado);
  I — isolamento entre um valor secreto e outro valor apenas PARECIDO
      (prefixo em comum), nunca mascarado por semelhança;
  J — regressão completa combinando body + query + header + múltiplos
      secrets + valores normais na MESMA request, ponta a ponta real.

Cadeia real (nos testes `*_e2e_*`/Cenário J):

    PostmanCollectionParser -> ApiAnalysisEngine -> TestStrategy
    -> PlaywrightEndpointTestGenerator -> DefaultPlaywrightTestSuiteBuilder
    -> arquivo .py físico -> Playwright/pytest real -> servidor HTTP real
    -> ExecutionResult -> PersistExecutionResultUseCase -> result.json
    -> JsonExecutionResultReader -> ReportEngine -> HTML

NÃO altera nenhum código de produção. NÃO implementa nenhuma correção.
NÃO altera Postman/Newman. Esta é uma tarefa de REGRESSÃO — a suíte
Postman/Newman é executada à parte (não neste arquivo) e comparada ao
baseline conhecido (ver relatório da tarefa).

DESVIO DOCUMENTADO: os requests com body usam `PUT`, não `POST` — o
servidor HTTP local real já existente no projeto
(tests/postman_test_server.py) só implementa `do_GET`/`do_PUT`. Criar
suporte a POST seria infraestrutura permanente nova só para este teste.

FRONTEIRAS MOCKADAS: nenhuma na geração. Na execução real, apenas o
repositório de persistência (`_RealFileRepository`, grava em tmp_path
real — mesmo padrão já usado nos outros arquivos desta família).
Playwright, pytest e o servidor HTTP são sempre reais.
"""

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from api_quality_agent.adapters.filesystem import JsonExecutionResultReader
from api_quality_agent.adapters.playwright import PlaywrightAdapter
from api_quality_agent.application.use_cases import PersistExecutionResultUseCase
from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    EnvironmentVariable,
    ExecutionContext,
    ExecutionMode,
    ExecutionResultLocation,
    PostmanEnvironment,
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

_STARTED_AT = datetime(2026, 8, 28, 14, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 28, 14, 0, 30, tzinfo=timezone.utc)

_USERNAME = "test-user"


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
                    "name": "P2.5 Regression",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "R", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _generated_endpoint(
    request: dict,
    environment: PostmanEnvironment | None = None,
    assertions: tuple[AssertionDefinition, ...] | None = None,
):
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions
        if assertions is not None
        else (
            AssertionDefinition(
                assertion_type=AssertionType.STATUS_CODE,
                description="Status code da resposta deve ser 200.",
                expected_value=200,
                origin="contract",
            ),
        ),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    # Componente REAL: PlaywrightEndpointTestGenerator, nunca mockado.
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request, environment)


def _write_single_endpoint_suite(tmp_path: Path, name: str, generated_endpoint) -> Path:
    execution_context = ExecutionContext.create(
        mode=ExecutionMode.OFFLINE,
        source="playwright-generation",
        workspace_id=None,
        collection_id="col-1",
        collection_name="P2.5 Regression",
        id_factory=lambda: f"exec-{name}",
    )
    # Componente REAL: DefaultPlaywrightTestSuiteBuilder, nunca mockado.
    suite = DefaultPlaywrightTestSuiteBuilder().build([generated_endpoint], execution_context)
    suite_dir = tmp_path / f"suite_{name}"
    for generated_file in suite.files:
        file_path = suite_dir / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(generated_file.content, encoding="utf-8")
    return suite_dir


def _endpoint_file_content(suite_dir: Path) -> str:
    # Inspeciona o arquivo FÍSICO realmente gerado em disco — nunca só a
    # string em memória de GeneratedEndpointTest.content.
    endpoint_files = sorted((suite_dir / "endpoints").glob("*.py"))
    assert len(endpoint_files) == 1, "suíte de um único endpoint deveria gerar um único arquivo"
    return endpoint_files[0].read_text(encoding="utf-8")


def _persist_read_report_html(result, run_dir: Path):
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))
    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="P2.5 Regression",
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


def _login_request(password_value: str) -> dict:
    return {
        "request": {
            "method": "PUT",
            "url": "https://api.exemplo.com/login",
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": {
                "mode": "raw",
                "raw": json.dumps({"username": _USERNAME, "password": password_value}),
            },
        }
    }


# ============================================================================
# CENÁRIO C — literal comum (não secreto): não é protegido, nunca inventa
# uma variável de ambiente para um valor que não corresponde a nada.
# ============================================================================


def test_cenario_c_literal_nao_secreto_permanece_intacto(tmp_path, monkeypatch):
    normal_value = "NORMAL_TEST_VALUE"
    # Environment presente (com OUTRO secret, "password"), mas nenhuma
    # variável tem esse valor — não há correspondência exata nenhuma.
    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(key="password", value="OTHER_SECRET_VALUE", is_secret=True, enabled=True),
        ),
    )
    generated = _generated_endpoint(_login_request(normal_value), environment)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario_c", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # .py continua contendo o valor normal — nada foi deferido.
    assert normal_value in content
    assert f'"password": "{normal_value}",' in content
    assert "AQO_PASSWORD" not in content
    assert generated.required_environment_variables == ()

    # Execução real: o valor normal chega ao servidor e permanece na
    # evidência normalmente — nenhuma máscara indevida.
    server = PostmanTestServer()
    try:
        server.set_route("/login", method="PUT", status=200, body={"ok": True})
        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario_c_e2e", generated)
        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        result = adapter.run(tests_path=str(suite_dir), timeout_seconds=90.0)

        assert result.infrastructure_failure is None
        assert result.success is True
        assert server.received_bodies[0] == {"username": _USERNAME, "password": normal_value}

        transaction = result.http_transactions[0]
        assert normal_value in (transaction.request_body or "")

        raw_payload, _, _, html = _persist_read_report_html(result, tmp_path / "run_cenario_c")
        assert normal_value in json.dumps(raw_payload)
        assert normal_value in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO D — secret embutido como SUBSTRING de um literal maior: NÃO é
# detectado (comportamento atual preservado deliberadamente — a
# correspondência é sempre por valor EXATO, nunca substring).
# ============================================================================


def test_cenario_d_secret_como_substring_nao_e_detectado(tmp_path):
    secret_value = "SECRET_123456"
    embedded_literal = f"Bearer {secret_value}"
    environment = PostmanEnvironment(
        name="QA",
        variables=(EnvironmentVariable(key="token", value=secret_value, is_secret=True, enabled=True),),
    )
    request = {
        "request": {
            "method": "PUT",
            "url": "https://api.exemplo.com/login",
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": {"mode": "raw", "raw": json.dumps({"value": embedded_literal})},
        }
    }
    generated = _generated_endpoint(request, environment)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario_d", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # "Bearer SECRET_123456" (valor completo) nunca é IGUAL a
    # "SECRET_123456" (o valor declarado como secreto) — nenhuma
    # correspondência exata, comportamento atual preservado sem alteração.
    assert embedded_literal in content
    assert f'"value": "{embedded_literal}",' in content
    assert "AQO_TOKEN" not in content
    assert generated.required_environment_variables == ()


# ============================================================================
# CENÁRIO G — Authorization com um valor que TAMBÉM bate com um secret do
# Environment: continua sendo omitido pela regra especial já existente
# (nunca deferido pelo mecanismo novo do P2.4).
# ============================================================================


def test_cenario_g_authorization_preservada_mesmo_com_secret_correspondente(tmp_path):
    secret_value = "SECRET_AUTH_VALUE_999"
    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(key="authToken", value=secret_value, is_secret=True, enabled=True),
        ),
    )
    request = {
        "request": {
            "method": "GET",
            "url": "https://api.exemplo.com/secure",
            "header": [{"key": "Authorization", "value": secret_value}],
        }
    }
    generated = _generated_endpoint(request, environment)
    ast.parse(generated.content)

    # Authorization é reconhecido ANTES de qualquer verificação de secret
    # correspondente (_RESERVED_HEADER_NAMES) — omitido com o MESMO código
    # de sempre, nunca deferido.
    assert secret_value not in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == "SENSITIVE_HEADER_OMITTED"
    assert "Authorization" not in generated.content.split('"""')[-1]  # nunca no dict de headers enviado
    assert "AQO_AUTH_TOKEN" not in generated.content  # nunca deferido


# ============================================================================
# CENÁRIO H — múltiplos secrets na MESMA request: a correção protege
# TODOS, não só o primeiro encontrado.
# ============================================================================


def test_cenario_h_multiplos_secrets_todos_protegidos(tmp_path, monkeypatch):
    password_value = "SECRET_PASSWORD_123"
    token_value = "SECRET_TOKEN_456"
    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(key="password", value=password_value, is_secret=True, enabled=True),
            EnvironmentVariable(key="token", value=token_value, is_secret=True, enabled=True),
        ),
    )
    request = {
        "request": {
            "method": "PUT",
            "url": "https://api.exemplo.com/login",
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": {
                "mode": "raw",
                "raw": json.dumps(
                    {
                        "username": _USERNAME,
                        "password": password_value,
                        "token": token_value,
                        "environment": "test",
                    }
                ),
            },
        }
    }
    generated = _generated_endpoint(request, environment)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario_h_gen", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # Nenhum dos dois secrets aparece no .py — não é só o primeiro.
    assert password_value not in content
    assert token_value not in content
    assert '"password": password,' in content
    assert '"token": token,' in content
    assert "AQO_PASSWORD" in content
    assert "AQO_TOKEN" in content
    # Valores não sensíveis intactos.
    assert f'"username": "{_USERNAME}",' in content
    assert '"environment": "test",' in content

    server = PostmanTestServer()
    try:
        server.set_route("/login", method="PUT", status=200, body={"ok": True})
        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario_h_e2e", generated)
        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        monkeypatch.setenv("AQO_PASSWORD", password_value)
        monkeypatch.setenv("AQO_TOKEN", token_value)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        result = adapter.run(
            tests_path=str(suite_dir),
            timeout_seconds=90.0,
            known_secret_values=(password_value, token_value),
        )

        assert result.infrastructure_failure is None, (
            f"{result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True
        # Ambos os secrets chegaram corretos ao servidor real.
        assert server.received_bodies[0] == {
            "username": _USERNAME,
            "password": password_value,
            "token": token_value,
            "environment": "test",
        }

        transaction = result.http_transactions[0]
        assert password_value not in (transaction.request_body or "")
        assert token_value not in (transaction.request_body or "")
        assert mask_secret(password_value) in (transaction.request_body or "")
        assert mask_secret(token_value) in (transaction.request_body or "")
        assert _USERNAME in (transaction.request_body or "")
        assert "test" in (transaction.request_body or "")

        raw_payload, _, _, html = _persist_read_report_html(result, tmp_path / "run_cenario_h")
        dumped = json.dumps(raw_payload)
        assert password_value not in dumped
        assert token_value not in dumped
        assert password_value not in html
        assert token_value not in html
        assert _USERNAME in dumped and _USERNAME in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO I — isolamento entre um valor secreto e outro apenas PARECIDO
# (prefixo em comum): correspondência exata, nunca por semelhança.
# ============================================================================


def test_cenario_i_correspondencia_exata_nunca_por_semelhanca(tmp_path):
    secret_value = "SECRET_TEST_123"
    similar_but_different_value = "SECRET_TEST_1234"  # prefixo igual, valor DIFERENTE
    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(key="secret_value", value=secret_value, is_secret=True, enabled=True),
        ),
    )
    request = {
        "request": {
            "method": "PUT",
            "url": "https://api.exemplo.com/login",
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": {
                "mode": "raw",
                "raw": json.dumps(
                    {"secret_value": secret_value, "normal_value": similar_but_different_value}
                ),
            },
        }
    }
    generated = _generated_endpoint(request, environment)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario_i", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # Só o valor EXATAMENTE igual ao declarado como secreto é protegido.
    # Checagem por FORMA LITERAL entre aspas (nunca `in` cru): o próprio
    # cenário usa dois valores propositalmente parecidos
    # (SECRET_TEST_123/SECRET_TEST_1234, um prefixo do outro), então uma
    # checagem por substring simples ("SECRET_TEST_123" in "...1234...")
    # daria falso positivo por estar contida no OUTRO valor, nunca porque
    # o valor secreto em si sobreviveu.
    quoted_secret_literal = f'"{secret_value}"'
    assert quoted_secret_literal not in content
    assert '"secret_value": secret_value,' in content
    assert "AQO_SECRET_VALUE" in content

    # O valor parecido, mas DIFERENTE, nunca é mascarado por semelhança —
    # continua um literal comum, visível no código gerado.
    assert similar_but_different_value in content
    assert f'"normal_value": "{similar_but_different_value}",' in content


# ============================================================================
# CENÁRIO J — regressão completa: body + query + header + múltiplos
# secrets + valores normais, na MESMA request, ponta a ponta real.
# ============================================================================


def test_cenario_j_regressao_completa_body_query_header(tmp_path, monkeypatch):
    password_value = "SECRET_PASSWORD_789"
    query_token_value = "SECRET_QUERY_123"
    header_token_value = "SECRET_HEADER_456"
    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(key="password", value=password_value, is_secret=True, enabled=True),
            EnvironmentVariable(key="token", value=query_token_value, is_secret=True, enabled=True),
            EnvironmentVariable(
                key="headerToken", value=header_token_value, is_secret=True, enabled=True
            ),
        ),
    )
    request = {
        "request": {
            "method": "PUT",
            "url": {
                "raw": f"https://api.exemplo.com/test?token={query_token_value}",
                "protocol": "https",
                "host": ["api", "exemplo", "com"],
                "path": ["test"],
                "query": [{"key": "token", "value": query_token_value}],
            },
            "header": [
                {"key": "Content-Type", "value": "application/json"},
                {"key": "X-Test-Token", "value": header_token_value},
            ],
            "body": {
                "mode": "raw",
                "raw": json.dumps(
                    {"username": _USERNAME, "password": password_value, "environment": "test"}
                ),
            },
        }
    }
    # Assertions continuam funcionando ao lado da proteção de secrets:
    # status + um valor de campo NÃO sensível (const), provando que a
    # correção não interfere na avaliação de assertions.
    assertions = (
        AssertionDefinition(
            assertion_type=AssertionType.STATUS_CODE,
            description="Status code da resposta deve ser 200.",
            expected_value=200,
            origin="contract",
        ),
        AssertionDefinition(
            assertion_type=AssertionType.VALID_JSON_BODY,
            description="O corpo da resposta deve ser um JSON válido.",
            expected_value=None,
            origin="contract",
        ),
    )
    generated = _generated_endpoint(request, environment, assertions)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario_j_gen", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # Nenhum dos três secrets aparece no .py.
    assert password_value not in content
    assert query_token_value not in content
    assert header_token_value not in content
    assert "AQO_PASSWORD" in content
    assert "AQO_TOKEN" in content
    assert "AQO_HEADER_TOKEN" in content
    # Valores normais intactos no código gerado.
    assert f'"username": "{_USERNAME}",' in content
    assert '"environment": "test",' in content

    server = PostmanTestServer()
    try:
        server.set_route(
            f"/test?token={query_token_value}", method="PUT", status=200, body={"ok": True}
        )
        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario_j_e2e", generated)
        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        monkeypatch.setenv("AQO_PASSWORD", password_value)
        monkeypatch.setenv("AQO_TOKEN", query_token_value)
        monkeypatch.setenv("AQO_HEADER_TOKEN", header_token_value)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        result = adapter.run(
            tests_path=str(suite_dir),
            timeout_seconds=90.0,
            known_secret_values=(password_value, query_token_value, header_token_value),
        )

        assert result.infrastructure_failure is None, (
            f"{result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        # ExecutionResult.success mantém seu significado normal (aqui,
        # sucesso — nada relacionado à proteção de secrets deveria alterar
        # essa semântica).
        assert result.success is True
        # Nenhum test_failure/evidence_failure artificial criado pela
        # proteção de secrets.
        assert result.test_failures == ()
        assert result.evidence_failures == ()

        # O servidor real recebeu os TRÊS valores corretos.
        assert len(server.received_paths) == 1
        assert server.received_paths[0] == f"/test?token={query_token_value}"
        assert server.received_bodies[0] == {
            "username": _USERNAME,
            "password": password_value,
            "environment": "test",
        }
        assert server.received_headers[0].get("X-Test-Token") == header_token_value

        # Assertions continuam funcionando (declaradas e avaliadas).
        assert len(result.assertion_results) >= 1
        status_assertion = next(a for a in result.assertion_results if a.name == "HTTP status")
        assert status_assertion.status == "PASSED"

        # Evidência: nenhum dos três secrets em claro; formas mascaradas
        # presentes onde a arquitetura já mascara (request_body/headers).
        transaction = result.http_transactions[0]
        query_values = {p.name: p.value for p in transaction.query_parameters}
        header_values = {h.name: h.value for h in transaction.request_headers}
        assert password_value not in (transaction.request_body or "")
        assert query_token_value not in (query_values.get("token") or "")
        assert header_token_value not in (header_values.get("X-Test-Token") or "")
        assert mask_secret(password_value) in (transaction.request_body or "")
        assert mask_secret(query_token_value) in (query_values.get("token") or "")
        assert mask_secret(header_token_value) in (header_values.get("X-Test-Token") or "")
        assert _USERNAME in (transaction.request_body or "")

        raw_payload, record, report, html = _persist_read_report_html(
            result, tmp_path / "run_cenario_j"
        )
        dumped = json.dumps(raw_payload)
        for secret in (password_value, query_token_value, header_token_value):
            assert secret not in dumped
            assert secret not in html
        assert _USERNAME in dumped and _USERNAME in html
        assert record.success is True
        assert len(report.execution.tests) == 1
    finally:
        server.shutdown()
