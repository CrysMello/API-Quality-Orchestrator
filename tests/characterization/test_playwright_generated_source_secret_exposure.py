"""Teste de caracterização E2E — exposição de dados sensíveis no
código-fonte .py gerado pelo fluxo Playwright.

Origem: achado nº2 do sanity check P2.3 (test_playwright_p2_pipeline_
sanity.py, Cenário J) — um secret literal usado como query parameter é
escrito em texto plano no arquivo .py gerado, porque o masking só
acontece DEPOIS da execução (PlaywrightAdapter lendo o NDJSON), nunca na
geração do código-fonte em si.

Este arquivo aprofunda aquele achado, respondendo precisamente:

1. O valor aparece no .py gerado? (query / header comum / header
   Authorization / body — a arquitetura trata os quatro campos de forma
   IGUAL ou DIFERENTE?)
2. Existe algum mecanismo real e já existente para fornecer o valor com
   segurança em runtime? (sim: `{{variável}}` + Environment marcado
   `is_secret=True` — testado e contrastado explicitamente contra o
   caminho vulnerável, usando a MESMA infraestrutura, nunca inventada
   aqui.)
3. Se a execução real acontecer, o servidor recebe o valor correto?
4. O masking pós-execução (result.json/HTML) resolve o problema do
   arquivo-fonte? (Não — nunca deve ser tratado como se resolvesse; este
   arquivo prova isso mostrando que o .py já vazou ANTES de qualquer
   masking rodar.)

NÃO corrige nada. NÃO modifica produção. NÃO modifica Postman/Newman.
Escopo exclusivamente Playwright.

FRONTEIRAS MOCKADAS: nenhuma na geração (Parser/ApiAnalysisEngine/
TestStrategy/Generator/SuiteBuilder reais). Na execução real (testes
`test_e2e_*`), a única fronteira mockada é o repositório de persistência
(`_RealFileRepository`, grava em tmp_path real — mesmo padrão de
tests/unit/test_http_evidence_round_trip.py). Playwright, pytest e o
servidor HTTP são sempre reais (`postman_test_server`).

IMPORTANTE SOBRE OS RESULTADOS "VERMELHOS": as asserções de segurança
para os campos SEM proteção hoje (query/body, mesmo quando a Collection
usa um Environment com o valor marcado secreto) são deliberadamente
escritas para esperar o comportamento SEGURO (valor ausente do .py) e
reportadas via `pytest.xfail(...)` quando o valor aparece — isso
preserva a suíte executável/reportável (nunca "1 failed" quebrando o
pipeline de validação), mas deixa o gap:
  - visível a cada execução (linha "xfailed" no resumo, com o motivo);
  - impossível de silenciar sem tocar neste arquivo;
  - garantido a chamar atenção se algum dia for corrigido (o mesmo teste
    então falha com `pytest.fail(...)`, forçando atualização consciente
    — nunca um "xpass" silencioso).
Nenhum teste foi removido, adaptado para aceitar o segredo, ou mascarado
manualmente antes da verificação do arquivo gerado.

Documenta o comportamento ATUAL — se este arquivo quebrar (xfail virando
fail, ou vice-versa) por uma mudança deliberada, atualize-o
conscientemente (ver tests/characterization/README.md).
"""

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
from postman_test_server import PostmanTestServer

_STARTED_AT = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 28, 10, 0, 30, tzinfo=timezone.utc)

# Valor claramente identificável e nunca um segredo real, conforme pedido.
SECRET_VALUE = "sk_live_TEST_SECRET_123456"

_ENV_WITH_MATCHING_SECRET = PostmanEnvironment(
    name="QA",
    variables=(EnvironmentVariable(key="apiKey", value=SECRET_VALUE, is_secret=True, enabled=True),),
)


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
                    "name": "Secret Exposure",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "R", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _status_assertion(status_code: int) -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
        origin="contract",
    )


def _generated_endpoint(
    request: dict,
    assertions: tuple[AssertionDefinition, ...],
    environment: PostmanEnvironment | None = None,
):
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions,
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
        collection_name="Secret Exposure",
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
    endpoint_files = sorted((suite_dir / "endpoints").glob("*.py"))
    assert len(endpoint_files) == 1, "suíte de um único endpoint deveria gerar um único arquivo"
    return endpoint_files[0].read_text(encoding="utf-8")


def _persist_read_report_html(result, run_dir: Path):
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))
    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="Secret Exposure",
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


def _query_request(url_path: str = "secure-data") -> dict:
    return {
        "request": {
            "method": "GET",
            "url": {
                "raw": f"https://api.exemplo.com/{url_path}?token={SECRET_VALUE}",
                "protocol": "https",
                "host": ["api", "exemplo", "com"],
                "path": [url_path],
                "query": [{"key": "token", "value": SECRET_VALUE}],
            },
        }
    }


def _header_custom_request() -> dict:
    return {
        "request": {
            "method": "GET",
            "url": "https://api.exemplo.com/secure-data",
            "header": [{"key": "X-Api-Key", "value": SECRET_VALUE}],
        }
    }


def _header_authorization_request() -> dict:
    return {
        "request": {
            "method": "GET",
            "url": "https://api.exemplo.com/secure-data",
            "header": [{"key": "Authorization", "value": f"Bearer {SECRET_VALUE}"}],
        }
    }


def _body_request() -> dict:
    return {
        "request": {
            "method": "PUT",
            "url": "https://api.exemplo.com/secure-data",
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": {"mode": "raw", "raw": json.dumps({"apiKey": SECRET_VALUE})},
        }
    }


# ============================================================================
# ETAPA 1/2 — Geração: o valor literal aparece no .py? O arquivo continua
# sintaticamente válido de qualquer forma? Query, header comum, header
# Authorization e body são tratados IGUALMENTE ou DIFERENTE?
# ============================================================================

_CASES = [
    pytest.param(_query_request(), None, False, id="query-literal-sem-environment"),
    pytest.param(_header_custom_request(), None, False, id="header-custom-literal-sem-environment"),
    pytest.param(
        _header_authorization_request(), None, True, id="header-authorization-literal-sem-environment"
    ),
    pytest.param(_body_request(), None, False, id="body-literal-sem-environment"),
    pytest.param(
        _header_custom_request(),
        _ENV_WITH_MATCHING_SECRET,
        True,
        id="header-custom-literal-bate-com-secret-do-environment",
    ),
    pytest.param(
        _query_request(),
        _ENV_WITH_MATCHING_SECRET,
        False,
        id="query-literal-bate-com-secret-do-environment-AINDA-EXPOSTO",
    ),
    pytest.param(
        _body_request(),
        _ENV_WITH_MATCHING_SECRET,
        False,
        id="body-literal-bate-com-secret-do-environment-AINDA-EXPOSTO",
    ),
]


@pytest.mark.parametrize("request_dict, environment, expected_secret_absent", _CASES)
def test_literal_sensitive_value_exposure_in_generated_source(
    tmp_path, request_dict, environment, expected_secret_absent
):
    generated = _generated_endpoint(request_dict, (_status_assertion(200),), environment)

    # ETAPA 2 — validade do código gerado: nunca aceitar como "solução" um
    # arquivo Python inválido ou um parâmetro simplesmente removido; o
    # mecanismo real de validação (ast.parse) é o mesmo já usado em todo o
    # restante da suíte de testes do gerador (ver test_playwright_endpoint_
    # test_generator.py e demais arquivos desta família).
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "gen_only", generated)
    written_content = _endpoint_file_content(suite_dir)
    ast.parse(written_content)  # revalida o arquivo físico, não só a string em memória

    secret_present = SECRET_VALUE in written_content

    if expected_secret_absent:
        # Caminho hoje considerado protegido (Authorization por nome
        # reservado; header comum cujo valor bate com um secret já
        # declarado no Environment, via _matches_known_secret).
        assert not secret_present, (
            "o valor sensível apareceu no .py gerado mesmo em um caminho "
            "considerado protegido pela arquitetura atual — isso É uma "
            "regressão real, não um achado esperado deste arquivo."
        )
        return

    # ETAPA 1 — critério de sucesso do teste: a asserção abaixo espera o
    # comportamento SEGURO (valor ausente). Sob o comportamento atual, ela
    # não se sustenta para query/body (com ou sem Environment) — o teste
    # não é adaptado para aceitar isso; o gap é reportado explicitamente.
    if secret_present:
        pytest.xfail(
            "GAP conhecido (achado nº2 do P2.3): SECRET_VALUE aparece "
            "literalmente no código .py gerado para este campo — a "
            "arquitetura atual não protege query parameters/body com "
            "valor literal (mesmo quando o MESMO valor já está declarado "
            "como secret em um Environment associado). Não corrigido "
            "nesta tarefa; ver relatório do teste de caracterização."
        )
    else:
        pytest.fail(
            "o gap documentado (SECRET_VALUE exposto no .py gerado) deixou "
            "de se reproduzir — atualize este teste de caracterização "
            "conscientemente (ver tests/characterization/README.md), "
            "trocando `expected_secret_absent` para True neste caso."
        )


# ============================================================================
# ETAPA 3-6 — Execução real do caminho VULNERÁVEL (query literal, sem
# Environment): confirma que o valor chega ao servidor corretamente, e que
# nada no restante do pipeline (result.json/HTML) protege o que já vazou
# no .py — o masking pós-execução não é (e não deve ser tratado como) uma
# correção para a exposição no código-fonte.
# ============================================================================


def test_e2e_literal_query_secret_reaches_the_server_and_is_not_protected_downstream(
    tmp_path, monkeypatch
):
    server = PostmanTestServer()
    try:
        server.set_route(f"/secure-data?token={SECRET_VALUE}", method="GET", status=200, body={"ok": True})

        generated = _generated_endpoint(_query_request(), (_status_assertion(200),))
        assert SECRET_VALUE in generated.content  # pré-condição: reproduz o gap da ETAPA 1

        suite_dir = _write_single_endpoint_suite(tmp_path, "e2e_vulnerable_query", generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        # known_secret_values=() DE PROPÓSITO (nunca omitido por engano):
        # em produção, nada popularia isto com um valor que só existe como
        # literal cru numa Collection — o mecanismo real de
        # known_secret_values vem de EnvironmentVariable.is_secret (ver
        # run_command.py), que este valor nunca teve.
        result = adapter.run(tests_path=str(suite_dir), timeout_seconds=90.0)

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True
        assert len(result.http_transactions) == 1
        transaction = result.http_transactions[0]
        assert transaction.test_id  # test_id correto/não vazio
        query_values = {p.name: p.value for p in transaction.query_parameters}
        # ETAPA 3: valor configurado -> request Playwright -> servidor HTTP
        # recebe o valor CORRETO (prova de que a cadeia funciona de ponta
        # a ponta; o problema não é funcional, é de segurança).
        assert query_values.get("token") == SECRET_VALUE

        # ETAPA 4: ExecutionResult captura tudo corretamente — mas o valor
        # sensível está em claro aqui também (sem known_secret_values,
        # nada mascara).
        assert SECRET_VALUE in json.dumps(
            [(p.name, p.value) for p in transaction.query_parameters]
        )

        raw_payload, record, report, html = _persist_read_report_html(
            result, tmp_path / "run_vulnerable"
        )
        # ETAPA 5/6: o valor sobrevive em claro até result.json e até o
        # HTML — nunca protegido, porque nunca foi identificado como
        # secreto em nenhum momento do pipeline.
        assert SECRET_VALUE in json.dumps(raw_payload)
        assert SECRET_VALUE in html
    finally:
        server.shutdown()


# ============================================================================
# CONTRASTE — o mecanismo SEGURO que já existe hoje: `{{variável}}` +
# Environment com a variável marcada `is_secret=True`. Nunca inventado
# aqui — é a mesma infraestrutura de VariableResolutionSession usada por
# toda a geração (ver playwright/variable_resolver.py). Prova que a
# arquitetura SABE proteger um valor quando a Collection é escrita da
# forma correta — o problema (achado da ETAPA 1) é exclusivo de quando um
# literal cru é usado em vez da referência de variável.
# ============================================================================


def test_e2e_variable_reference_query_secret_is_never_written_literally_and_resolves_safely(
    tmp_path, monkeypatch
):
    server = PostmanTestServer()
    try:
        server.set_route(f"/secure-data?token={SECRET_VALUE}", method="GET", status=200, body={"ok": True})

        request = {
            "request": {
                "method": "GET",
                "url": {
                    "raw": "https://api.exemplo.com/secure-data?token={{apiKey}}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["secure-data"],
                    "query": [{"key": "token", "value": "{{apiKey}}"}],
                },
            }
        }
        generated = _generated_endpoint(
            request, (_status_assertion(200),), _ENV_WITH_MATCHING_SECRET
        )

        # ETAPA 1 (contraste): usando {{variável}} + Environment secreto,
        # o valor NUNCA é escrito literalmente — deferido para
        # os.environ.get("AQO_API_KEY") em runtime.
        assert SECRET_VALUE not in generated.content
        assert "AQO_API_KEY" in generated.content
        assert "AQO_API_KEY" in generated.required_environment_variables
        ast.parse(generated.content)  # ETAPA 2: continua válido

        suite_dir = _write_single_endpoint_suite(tmp_path, "e2e_safe_query", generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        # Mecanismo real e já existente: o valor chega em runtime via
        # variável de ambiente do SO — nunca uma infraestrutura nova
        # criada para este teste.
        monkeypatch.setenv("AQO_API_KEY", SECRET_VALUE)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        # known_secret_values populado explicitamente aqui simula o que
        # run_command.py já faz em produção: junta os valores de
        # EnvironmentVariable.is_secret antes de chamar o adapter.
        result = adapter.run(
            tests_path=str(suite_dir), timeout_seconds=90.0, known_secret_values=(SECRET_VALUE,)
        )

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True
        transaction = result.http_transactions[0]
        query_values = {p.name: p.value for p in transaction.query_parameters}
        # ETAPA 3: valor real chega ao servidor (a rota só responde 200
        # para a query string com o valor correto).
        assert SECRET_VALUE not in (query_values.get("token") or "")
        assert "****" in (query_values.get("token") or "")  # mascarado, não removido

        raw_payload, record, report, html = _persist_read_report_html(result, tmp_path / "run_safe")
        assert SECRET_VALUE not in json.dumps(raw_payload)
        assert SECRET_VALUE not in html
    finally:
        server.shutdown()
