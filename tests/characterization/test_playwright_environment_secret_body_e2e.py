"""Teste de caracterização E2E — tratamento de um dado sensível
referenciado por variável de Environment (`{{password}}`) dentro do BODY
JSON de um request, no fluxo Playwright.

Complementa (nunca duplica) test_playwright_generated_source_secret_
exposure.py: aquele arquivo prova que um valor LITERAL colado direto na
Collection vaza no .py gerado (query/body) mesmo quando um Environment
declara o mesmo valor como secreto (`_matches_known_secret` só existe
para headers). Este arquivo valida o outro lado da mesma moeda: quando a
Collection usa CORRETAMENTE uma referência `{{variável}}` para uma
variável de Environment marcada `is_secret=True`, o gerador Playwright já
sabe proteger o valor — nunca embutido no código, sempre deferido para
`os.environ.get("AQO_<NOME>")` em runtime (mesmo VariableResolutionSession
central usado por header/query/body, ver playwright/variable_resolver.py
e _render_json_literal em playwright_endpoint_test_generator.py).

Cadeia validada:

    Environment -> Collection ({{password}}) -> PostmanCollectionParser
    -> ApiAnalysisEngine -> TestStrategy -> PlaywrightEndpointTestGenerator
    -> código .py gerado -> Playwright real -> servidor HTTP real
    -> PlaywrightAdapter -> ExecutionResult -> result.json
    -> JsonExecutionResultReader -> ReportEngine -> HTML

NÃO corrige nada (não há nada a corrigir aqui — o mecanismo já funciona
corretamente para este caso). NÃO altera produção. NÃO altera Postman/
Newman. Escopo exclusivamente Playwright.

DESVIO DOCUMENTADO EM RELAÇÃO AO ENUNCIADO: o cenário pedido usa
`POST /login`, mas o servidor HTTP local real já existente no projeto
(tests/postman_test_server.py) só implementa `do_GET`/`do_PUT` — nunca
`do_POST`. Adicionar suporte a POST seria criar infraestrutura permanente
nova só para viabilizar este teste, o que o enunciado pede explicitamente
para não fazer. Por isso o endpoint usado aqui é `PUT /login` — a
resolução de `{{password}}` dentro do body (_render_json_literal ->
VariableResolutionSession.resolve) é inteiramente independente do verbo
HTTP, então a substituição não muda em nada o que está sendo validado.

FRONTEIRAS MOCKADAS: nenhuma na geração (Parser/ApiAnalysisEngine/
TestStrategy/Generator/SuiteBuilder reais). Na execução real, apenas o
repositório de persistência (`_RealFileRepository`, grava em tmp_path
real — mesmo padrão já usado em vários arquivos desta suíte). Playwright,
pytest e o servidor HTTP são sempre reais.

Documenta o comportamento ATUAL — se quebrar por uma mudança deliberada,
atualize-o conscientemente (ver tests/characterization/README.md).
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

_STARTED_AT = datetime(2026, 8, 28, 11, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 28, 11, 0, 30, tzinfo=timezone.utc)

# Valor fictício, exclusivo deste teste — nunca uma credencial real.
SECRET_VALUE = "SECRET_TEST_VALUE_123456"
_MASKED_SECRET = mask_secret(SECRET_VALUE)
_USERNAME = "test-user"

_ENVIRONMENT = PostmanEnvironment(
    name="QA",
    variables=(EnvironmentVariable(key="password", value=SECRET_VALUE, is_secret=True, enabled=True),),
)

_LOGIN_REQUEST = {
    "request": {
        # PUT em vez de POST — ver docstring do módulo ("DESVIO
        # DOCUMENTADO").
        "method": "PUT",
        "url": "https://api.exemplo.com/login",
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": {
            "mode": "raw",
            "raw": json.dumps({"username": _USERNAME, "password": "{{password}}"}),
        },
    }
}


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
                    "name": "Environment Secret Body",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "Login", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _generated_endpoint(request: dict, environment: PostmanEnvironment | None):
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


def _write_single_endpoint_suite(tmp_path: Path, generated_endpoint) -> Path:
    execution_context = ExecutionContext.create(
        mode=ExecutionMode.OFFLINE,
        source="playwright-generation",
        workspace_id=None,
        collection_id="col-1",
        collection_name="Environment Secret Body",
        id_factory=lambda: "exec-env-secret-body",
    )
    # Componente REAL: DefaultPlaywrightTestSuiteBuilder, nunca mockado.
    suite = DefaultPlaywrightTestSuiteBuilder().build([generated_endpoint], execution_context)
    suite_dir = tmp_path / "suite"
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
        collection_name="Environment Secret Body",
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
# ETAPA 1 — Geração: o segredo referenciado por variável de Environment
# NUNCA aparece no .py gerado; e a informação necessária para obter o
# valor em runtime (nome da variável de ambiente, chave "password" no
# body) continua presente — nunca eliminada, nunca substituída por ""/
# "****", nunca um código inválido.
# ============================================================================


def test_etapa1_password_variable_is_never_written_literally_but_remains_resolvable(tmp_path):
    generated = _generated_endpoint(_LOGIN_REQUEST, _ENVIRONMENT)

    # ETAPA 2 (validade do código): mecanismo real já usado em toda a
    # suíte de testes do gerador — nunca aceitar um .py inválido como
    # "proteção".
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, generated)
    written_content = _endpoint_file_content(suite_dir)
    ast.parse(written_content)  # revalida o arquivo físico em disco

    # O segredo em si nunca aparece literalmente.
    assert SECRET_VALUE not in written_content

    # A request NÃO foi quebrada: a chave "password" continua presente no
    # body (nunca removida, nunca virou "" nem "****" hardcoded) — só o
    # VALOR foi deferido para uma variável resolvida em runtime.
    assert '"password": password,' in written_content
    assert '"password": "",' not in written_content
    assert '"password": "****",' not in written_content
    assert '"password"' in written_content  # chave presente, em qualquer forma

    # A informação para obter o valor em runtime está presente, nunca
    # eliminada: nome da variável de ambiente + leitura + validação
    # explícita de que ela precisa estar definida.
    assert "AQO_PASSWORD" in written_content
    assert 'os.environ.get("AQO_PASSWORD")' in written_content
    assert "AQO_PASSWORD" in generated.required_environment_variables

    # ETAPA 6 (isolamento, já na geração): o dado NÃO sensível continua
    # disponível corretamente — nunca uma "limpeza" genérica do body
    # inteiro por causa do campo sensível ao lado.
    assert f'"username": "{_USERNAME}",' in written_content


# ============================================================================
# ETAPA 2-6 — Execução real (Playwright real, pytest real, servidor HTTP
# real), fornecendo o valor da variável de Environment via o único
# mecanismo real e já existente para isso: a variável de ambiente do SO
# lida em runtime pelo próprio código gerado (AQO_PASSWORD). Nenhuma
# infraestrutura nova foi criada para isto — é exatamente o preâmbulo já
# emitido na ETAPA 1.
# ============================================================================


def test_etapa2a6_real_execution_delivers_the_real_secret_and_keeps_it_masked_downstream(
    tmp_path, monkeypatch
):
    server = PostmanTestServer()
    try:
        server.set_route(
            "/login", method="PUT", status=200, body={"token": "fake-jwt-not-a-real-secret"}
        )

        generated = _generated_endpoint(_LOGIN_REQUEST, _ENVIRONMENT)
        assert SECRET_VALUE not in generated.content  # pré-condição da ETAPA 1

        suite_dir = _write_single_endpoint_suite(tmp_path, generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        # Mecanismo real e já existente: a variável chega em runtime via
        # variável de ambiente do SO — nunca uma infraestrutura nova.
        monkeypatch.setenv("AQO_PASSWORD", SECRET_VALUE)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        # known_secret_values populado explicitamente aqui simula o que
        # run_command.py já faz em produção: reúne os valores de
        # EnvironmentVariable.is_secret antes de chamar o adapter.
        result = adapter.run(
            tests_path=str(suite_dir), timeout_seconds=90.0, known_secret_values=(SECRET_VALUE,)
        )

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True

        # ETAPA 2 (confirmação no servidor): o servidor real recebeu o
        # BODY completo com o valor VERDADEIRO da senha — prova de que a
        # proteção na geração não quebrou o funcionamento em runtime.
        assert len(server.received_bodies) == 1
        assert server.received_bodies[0] == {"username": _USERNAME, "password": SECRET_VALUE}

        # ETAPA 3 — HttpTransaction capturada.
        assert len(result.http_transactions) == 1
        transaction = result.http_transactions[0]
        assert transaction.test_id  # test_id correto/não vazio
        assert transaction.method == "PUT"
        assert transaction.url.endswith("/login")
        assert transaction.request_body is not None
        # O corpo capturado como evidência já chega mascarado do
        # PlaywrightAdapter — nunca o valor em claro aqui.
        assert SECRET_VALUE not in transaction.request_body
        assert _MASKED_SECRET in transaction.request_body
        # Isolamento (ETAPA 6): o dado não sensível continua correto na
        # evidência.
        assert _USERNAME in transaction.request_body

        raw_payload, record, report, html = _persist_read_report_html(result, tmp_path / "run")

        # ETAPA 4 — result.json.
        assert SECRET_VALUE not in json.dumps(raw_payload)
        assert _MASKED_SECRET in json.dumps(raw_payload)

        # ETAPA 5 — HTML.
        assert SECRET_VALUE not in html
        assert _MASKED_SECRET in html

        # Isolamento (ETAPA 6), de ponta a ponta: o username nunca é
        # afetado pela proteção do secret vizinho, em nenhuma camada.
        assert _USERNAME in json.dumps(raw_payload)
        assert _USERNAME in html
        reread_transaction = record.http_transactions[0]
        assert _USERNAME in (reread_transaction.request_body or "")
        assert SECRET_VALUE not in (reread_transaction.request_body or "")
    finally:
        server.shutdown()
