"""Teste de caracterização E2E — proteção de dados sensíveis literais na
geração de testes Playwright (body/query/header).

HISTÓRICO: este arquivo originalmente comprovava um bug de segurança —
um valor sensível colado diretamente como literal cru na Collection (sem
usar `{{variável}}`) era materializado em texto plano no `.py` gerado e
sobrevivia em claro até `result.json`/HTML, mesmo quando o MESMO valor já
estava declarado como secreto (`is_secret=True`) em um `PostmanEnvironment`
associado. Corrigido no bloco P2.4 (`playwright_endpoint_test_generator.py`
— `_find_matching_secret_variable_name`, reaproveitando exatamente o
`VariableResolutionSession.resolve()` já usado para `{{variável}}`, nunca
um segundo mecanismo de masking). Este arquivo foi atualizado
conscientemente (ver tests/characterization/README.md) para validar o
comportamento CORRIGIDO, cobrindo os três campos onde a correção se
aplica: JSON body, query parameter e header.

Cadeia validada (real, ponta a ponta, nos testes `*_e2e_*`):

    PostmanCollectionParser -> ApiAnalysisEngine -> TestStrategy
    -> PlaywrightEndpointTestGenerator -> DefaultPlaywrightTestSuiteBuilder
    -> arquivo .py físico -> Playwright/pytest real -> servidor HTTP real
    -> ExecutionResult -> PersistExecutionResultUseCase -> result.json
    -> JsonExecutionResultReader -> ReportEngine -> HTML

NÃO altera Generator/PlaywrightAdapter/PersistExecutionResultUseCase/
ReportEngine nesta tarefa (a correção já foi aplicada separadamente ao
Generator — este arquivo só valida o resultado). NÃO implementa masking
novo. NÃO altera Postman/Newman.

DESVIO DOCUMENTADO: os requests de body usam `PUT /login`, não
`POST /login` — o servidor HTTP local real já existente no projeto
(tests/postman_test_server.py) só implementa `do_GET`/`do_PUT`, nunca
`do_POST`. Criar suporte a POST seria infraestrutura permanente nova só
para este teste.

FRONTEIRAS MOCKADAS: nenhuma na geração. Na execução real, apenas o
repositório de persistência (`_RealFileRepository`, grava em tmp_path
real). Playwright, pytest e o servidor HTTP são sempre reais. Nenhum
parsing artificial de URL/body é feito para simular masking — todas as
verificações leem exatamente o que o pipeline real já produziu.
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

_STARTED_AT = datetime(2026, 8, 28, 13, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 28, 13, 0, 30, tzinfo=timezone.utc)

_USERNAME = "test-user"
# Valores fictícios, exclusivos deste teste — nunca uma credencial real.
_SECRET_VARIABLE_VALUE = "SECRET_TEST_VALUE_123456"  # Cenário 1: {{password}}
_SECRET_LITERAL_VALUE = "SECRET_LITERAL_TEST_123456"  # Cenário 2: literal correspondente a um secret
_UNKNOWN_LITERAL_VALUE = "UNKNOWN_LITERAL_NOT_A_SECRET_999"  # controle: literal SEM correspondência
_MASKED_VARIABLE_SECRET = mask_secret(_SECRET_VARIABLE_VALUE)
_MASKED_LITERAL_SECRET = mask_secret(_SECRET_LITERAL_VALUE)

_ENVIRONMENT_FOR_VARIABLE = PostmanEnvironment(
    name="QA",
    variables=(
        EnvironmentVariable(key="password", value=_SECRET_VARIABLE_VALUE, is_secret=True, enabled=True),
    ),
)
_ENVIRONMENT_MATCHING_BODY_LITERAL = PostmanEnvironment(
    name="QA",
    variables=(
        EnvironmentVariable(key="password", value=_SECRET_LITERAL_VALUE, is_secret=True, enabled=True),
    ),
)
_ENVIRONMENT_MATCHING_QUERY_LITERAL = PostmanEnvironment(
    name="QA",
    variables=(
        EnvironmentVariable(key="apiToken", value=_SECRET_LITERAL_VALUE, is_secret=True, enabled=True),
    ),
)
_ENVIRONMENT_MATCHING_HEADER_LITERAL = PostmanEnvironment(
    name="QA",
    variables=(
        EnvironmentVariable(key="testToken", value=_SECRET_LITERAL_VALUE, is_secret=True, enabled=True),
    ),
)


class _RealFileRepository:
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def save(self, *, content: str) -> ExecutionResultLocation:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / "result.json"
        path.write_text(content, encoding="utf-8")
        return ExecutionResultLocation(path=str(path))


def _login_request(password_value: str) -> dict:
    return {
        "request": {
            # PUT em vez de POST — ver docstring do módulo ("DESVIO
            # DOCUMENTADO").
            "method": "PUT",
            "url": "https://api.exemplo.com/login",
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": {
                "mode": "raw",
                "raw": json.dumps({"username": _USERNAME, "password": password_value}),
            },
        }
    }


_VARIABLE_REQUEST = _login_request("{{password}}")
_LITERAL_BODY_REQUEST = _login_request(_SECRET_LITERAL_VALUE)
_LITERAL_UNKNOWN_BODY_REQUEST = _login_request(_UNKNOWN_LITERAL_VALUE)

_LITERAL_QUERY_REQUEST = {
    "request": {
        "method": "GET",
        "url": {
            "raw": f"https://api.exemplo.com/users?token={_SECRET_LITERAL_VALUE}&name={_USERNAME}",
            "protocol": "https",
            "host": ["api", "exemplo", "com"],
            "path": ["users"],
            "query": [
                {"key": "token", "value": _SECRET_LITERAL_VALUE},
                {"key": "name", "value": _USERNAME},
            ],
        },
    }
}

_LITERAL_HEADER_REQUEST = {
    "request": {
        "method": "GET",
        "url": "https://api.exemplo.com/secure",
        "header": [
            {"key": "X-Test-Token", "value": _SECRET_LITERAL_VALUE},
            {"key": "X-Other", "value": "not-secret"},
        ],
    }
}


def _analyzed(request: dict):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Literal Secret E2E",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "R", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _generated_endpoint(request: dict, environment: PostmanEnvironment | None = None):
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=(
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
        collection_name="Literal Secret E2E",
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
    # Inspeciona o arquivo FÍSICO realmente gerado em disco.
    endpoint_files = sorted((suite_dir / "endpoints").glob("*.py"))
    assert len(endpoint_files) == 1, "suíte de um único endpoint deveria gerar um único arquivo"
    return endpoint_files[0].read_text(encoding="utf-8")


def _persist_read_report_html(result, run_dir: Path):
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))
    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="Literal Secret E2E",
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


# ============================================================================
# CENÁRIO 1 — secret referenciado por variável de Environment ({{password}})
# — continua protegido depois da correção (nada mudou neste caminho).
# ============================================================================


def test_cenario1_variavel_continua_protegida_na_geracao(tmp_path):
    generated = _generated_endpoint(_VARIABLE_REQUEST, _ENVIRONMENT_FOR_VARIABLE)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario1_gen", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    assert _SECRET_VARIABLE_VALUE not in content
    assert '"password": password,' in content
    assert "AQO_PASSWORD" in content
    assert "AQO_PASSWORD" in generated.required_environment_variables
    assert f'"username": "{_USERNAME}",' in content  # valor não sensível intacto


def test_cenario1_e2e_variavel_protegida_e_funcional(tmp_path, monkeypatch):
    server = PostmanTestServer()
    try:
        server.set_route(
            "/login", method="PUT", status=200, body={"token": "fake-jwt-not-a-real-secret"}
        )
        generated = _generated_endpoint(_VARIABLE_REQUEST, _ENVIRONMENT_FOR_VARIABLE)
        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario1_e2e", generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        monkeypatch.setenv("AQO_PASSWORD", _SECRET_VARIABLE_VALUE)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        result = adapter.run(
            tests_path=str(suite_dir),
            timeout_seconds=90.0,
            known_secret_values=(_SECRET_VARIABLE_VALUE,),
        )

        assert result.infrastructure_failure is None
        assert result.success is True
        assert server.received_bodies[0] == {"username": _USERNAME, "password": _SECRET_VARIABLE_VALUE}

        transaction = result.http_transactions[0]
        assert _SECRET_VARIABLE_VALUE not in (transaction.request_body or "")
        assert _MASKED_VARIABLE_SECRET in (transaction.request_body or "")

        raw_payload, _, _, html = _persist_read_report_html(result, tmp_path / "run_cenario1")
        assert _SECRET_VARIABLE_VALUE not in json.dumps(raw_payload)
        assert _SECRET_VARIABLE_VALUE not in html
        assert _MASKED_VARIABLE_SECRET in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 2a — secret literal no JSON BODY, correspondente a um secret do
# Environment — CORRIGIDO: agora protegido, exatamente como o Cenário 1.
# ============================================================================


def test_cenario2_body_literal_correspondente_a_secret_agora_protegido(tmp_path):
    generated = _generated_endpoint(_LITERAL_BODY_REQUEST, _ENVIRONMENT_MATCHING_BODY_LITERAL)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_body_gen", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # 1. O valor real não aparece mais no .py gerado.
    assert _SECRET_LITERAL_VALUE not in content
    # A request continua funcional: chave "password" preservada, deferida
    # para runtime via o MESMO mecanismo de {{variable}} — nunca removida,
    # nunca "" / "****".
    assert '"password": password,' in content
    assert "AQO_PASSWORD" in content
    assert 'os.environ.get("AQO_PASSWORD")' in content
    assert "AQO_PASSWORD" in generated.required_environment_variables
    # Valor não sensível ao lado permanece intacto.
    assert f'"username": "{_USERNAME}",' in content


def test_cenario2_body_literal_e2e_protegido_e_funcional(tmp_path, monkeypatch):
    # TESTE E2E OBRIGATÓRIO: cadeia completa e real, provando que o
    # literal protegido na geração continua funcionando em runtime e
    # nunca aparece em claro em nenhuma evidência a jusante.
    server = PostmanTestServer()
    try:
        server.set_route(
            "/login", method="PUT", status=200, body={"token": "fake-jwt-not-a-real-secret"}
        )

        generated = _generated_endpoint(_LITERAL_BODY_REQUEST, _ENVIRONMENT_MATCHING_BODY_LITERAL)
        assert _SECRET_LITERAL_VALUE not in generated.content  # pré-condição (correção confirmada)

        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_body_e2e", generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        monkeypatch.setenv("AQO_PASSWORD", _SECRET_LITERAL_VALUE)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        result = adapter.run(
            tests_path=str(suite_dir),
            timeout_seconds=90.0,
            known_secret_values=(_SECRET_LITERAL_VALUE,),
        )

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True

        # 4. O servidor recebe o valor VERDADEIRO — a proteção não quebrou
        # a request.
        assert len(server.received_bodies) == 1
        assert server.received_bodies[0] == {"username": _USERNAME, "password": _SECRET_LITERAL_VALUE}

        # 3. http_transactions: valor real ausente, forma mascarada presente.
        assert len(result.http_transactions) == 1
        transaction = result.http_transactions[0]
        assert transaction.request_body is not None
        assert _SECRET_LITERAL_VALUE not in transaction.request_body
        assert _MASKED_LITERAL_SECRET in transaction.request_body
        assert _USERNAME in transaction.request_body  # não sensível intacto

        raw_payload, record, report, html = _persist_read_report_html(
            result, tmp_path / "run_cenario2_body"
        )

        # 5. result.json sem o valor real; forma mascarada presente.
        assert _SECRET_LITERAL_VALUE not in json.dumps(raw_payload)
        assert _MASKED_LITERAL_SECRET in json.dumps(raw_payload)

        # 6. HTML sem o valor real; forma mascarada presente.
        assert _SECRET_LITERAL_VALUE not in html
        assert _MASKED_LITERAL_SECRET in html

        # Valor não sensível intacto em toda a cadeia.
        assert _USERNAME in json.dumps(raw_payload)
        assert _USERNAME in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 2b — secret literal em QUERY PARAMETER, correspondente a um
# secret do Environment — CORRIGIDO.
# ============================================================================


def test_cenario2_query_literal_correspondente_a_secret_agora_protegido(tmp_path):
    generated = _generated_endpoint(_LITERAL_QUERY_REQUEST, _ENVIRONMENT_MATCHING_QUERY_LITERAL)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_query_gen", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    assert _SECRET_LITERAL_VALUE not in content
    assert "AQO_API_TOKEN" in content
    assert '"token": api_token,' in content
    # name=test-user (não sensível) continua um literal comum, intacto.
    assert f'"name": "{_USERNAME}",' in content


def test_cenario2_query_literal_e2e_protegido_e_funcional(tmp_path, monkeypatch):
    server = PostmanTestServer()
    try:
        server.set_route(
            f"/users?token={_SECRET_LITERAL_VALUE}&name={_USERNAME}",
            method="GET",
            status=200,
            body={"ok": True},
        )

        generated = _generated_endpoint(_LITERAL_QUERY_REQUEST, _ENVIRONMENT_MATCHING_QUERY_LITERAL)
        assert _SECRET_LITERAL_VALUE not in generated.content

        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_query_e2e", generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        monkeypatch.setenv("AQO_API_TOKEN", _SECRET_LITERAL_VALUE)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        result = adapter.run(
            tests_path=str(suite_dir),
            timeout_seconds=90.0,
            known_secret_values=(_SECRET_LITERAL_VALUE,),
        )

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True

        assert len(result.http_transactions) == 1
        transaction = result.http_transactions[0]
        query_values = {p.name: p.value for p in transaction.query_parameters}
        # O servidor só respondeu 200 para a query string EXATA com o
        # valor real — logo, o valor real chegou (a rota exige isso).
        assert transaction.response_status == 200
        # A evidência pós-execução já chega mascarada.
        assert _SECRET_LITERAL_VALUE not in (query_values.get("token") or "")
        assert _MASKED_LITERAL_SECRET in (query_values.get("token") or "")
        assert query_values.get("name") == _USERNAME  # não sensível intacto

        raw_payload, record, report, html = _persist_read_report_html(
            result, tmp_path / "run_cenario2_query"
        )
        assert _SECRET_LITERAL_VALUE not in json.dumps(raw_payload)
        assert _SECRET_LITERAL_VALUE not in html
        assert _MASKED_LITERAL_SECRET in html
        assert _USERNAME in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 2c — secret literal em HEADER, correspondente a um secret do
# Environment — CORRIGIDO (mesma correspondência por valor já usada para
# Authorization/headers reservados, agora deferindo em vez de omitir).
# ============================================================================


def test_cenario2_header_literal_correspondente_a_secret_agora_protegido(tmp_path):
    generated = _generated_endpoint(_LITERAL_HEADER_REQUEST, _ENVIRONMENT_MATCHING_HEADER_LITERAL)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_header_gen", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    assert _SECRET_LITERAL_VALUE not in content
    # Nunca mais omitido (o que quebraria a request) — deferido, o header
    # continua presente no dict enviado.
    assert '"X-Test-Token": test_token,' in content
    assert "AQO_TEST_TOKEN" in content
    # Header não sensível ao lado permanece intacto.
    assert '"X-Other": "not-secret",' in content


def test_cenario2_header_literal_e2e_protegido_e_funcional(tmp_path, monkeypatch):
    server = PostmanTestServer()
    try:
        server.set_route("/secure", method="GET", status=200, body={"ok": True})

        generated = _generated_endpoint(_LITERAL_HEADER_REQUEST, _ENVIRONMENT_MATCHING_HEADER_LITERAL)
        assert _SECRET_LITERAL_VALUE not in generated.content

        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_header_e2e", generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        monkeypatch.setenv("AQO_TEST_TOKEN", _SECRET_LITERAL_VALUE)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        result = adapter.run(
            tests_path=str(suite_dir),
            timeout_seconds=90.0,
            known_secret_values=(_SECRET_LITERAL_VALUE,),
        )

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True

        # O servidor de fato recebeu a chamada (header não impediu o envio
        # da request — a proteção não quebrou o funcionamento).
        assert len(server.received_paths) == 1
        received_headers = server.received_headers[0]
        # O valor real chegou de verdade no header HTTP enviado.
        assert received_headers.get("X-Test-Token") == _SECRET_LITERAL_VALUE
        assert received_headers.get("X-Other") == "not-secret"

        assert len(result.http_transactions) == 1
        transaction = result.http_transactions[0]
        request_header_values = {h.name: h.value for h in transaction.request_headers}
        assert _SECRET_LITERAL_VALUE not in json.dumps(request_header_values)
        assert _MASKED_LITERAL_SECRET in (request_header_values.get("X-Test-Token") or "")
        assert request_header_values.get("X-Other") == "not-secret"

        raw_payload, record, report, html = _persist_read_report_html(
            result, tmp_path / "run_cenario2_header"
        )
        assert _SECRET_LITERAL_VALUE not in json.dumps(raw_payload)
        assert _SECRET_LITERAL_VALUE not in html
    finally:
        server.shutdown()


# ============================================================================
# CONTROLE — um literal que NÃO corresponde a nenhum secret declarado no
# Environment nunca deve ser tratado como secreto (a correção não pode
# "inventar" que um valor é sensível sem essa correspondência exata).
# ============================================================================


def test_literal_sem_correspondencia_no_environment_nao_e_tratado_como_secret(tmp_path):
    generated = _generated_endpoint(_LITERAL_UNKNOWN_BODY_REQUEST, _ENVIRONMENT_MATCHING_BODY_LITERAL)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "controle_sem_match", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # O Environment declara "password" como secreto (valor
    # SECRET_LITERAL_TEST_123456), mas o literal usado aqui é OUTRO valor
    # (UNKNOWN_LITERAL_NOT_A_SECRET_999) — nenhuma correspondência exata,
    # então continua um literal comum, nunca deferido artificialmente.
    assert _UNKNOWN_LITERAL_VALUE in content
    assert f'"password": "{_UNKNOWN_LITERAL_VALUE}",' in content
    assert "AQO_PASSWORD" not in content
    assert generated.required_environment_variables == ()
