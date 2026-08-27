"""Teste de caracterização E2E — comparação direta entre PROTEÇÃO (secret
referenciado por `{{variável}}` de Environment) e EXPOSIÇÃO (o mesmo tipo
de secret colocado como literal cru na Collection) na geração Playwright.

Contexto: o fluxo Playwright já protege corretamente uma variável de
Environment marcada `is_secret=True` quando a Collection usa uma
referência como `{{password}}` (ver test_playwright_environment_secret_
body_e2e.py). Em execução real (ver test_playwright_generated_source_
secret_exposure.py e o teste manual contra a Collection real do
FakeStoreAPI), foi identificado que um valor sensível colado diretamente
como literal no Body pode aparecer no arquivo .py gerado. Este arquivo
caracteriza os DOIS cenários lado a lado, na MESMA estrutura de request
(login com username/password), para que a diferença de tratamento fique
inequívoca.

Cadeia validada (real, ponta a ponta):

    PostmanCollectionParser -> ApiAnalysisEngine -> TestStrategy
    -> PlaywrightEndpointTestGenerator -> DefaultPlaywrightTestSuiteBuilder
    -> arquivo .py gerado -> Playwright/pytest real -> servidor HTTP real
    -> ExecutionResult -> PersistExecutionResultUseCase -> result.json
    -> JsonExecutionResultReader -> ReportEngine -> HTML

NÃO corrige nada. NÃO altera Generator/PlaywrightAdapter/
PersistExecutionResultUseCase/ReportEngine. NÃO implementa masking novo.
NÃO altera Postman/Newman. NÃO altera nenhum teste existente.

DESVIO DOCUMENTADO: os requests usam `PUT /login`, não `POST /login`. O
servidor HTTP local real já existente no projeto
(tests/postman_test_server.py) só implementa `do_GET`/`do_PUT` — nunca
`do_POST`. Criar suporte a POST seria adicionar infraestrutura permanente
nova só para este teste, o que foi explicitamente pedido para não fazer.
A resolução de valor dentro do body (`_render_json_literal`) é
inteiramente independente do verbo HTTP, então a troca não afeta em nada
o que está sendo validado.

FRONTEIRAS MOCKADAS: nenhuma na geração (Parser/ApiAnalysisEngine/
TestStrategy/Generator/SuiteBuilder reais, nunca mockados). Na execução
real, apenas o repositório de persistência (`_RealFileRepository`, grava
em tmp_path real — mesmo padrão já usado em outros arquivos desta
suíte). Playwright, pytest e o servidor HTTP são sempre reais. Nenhum
parsing artificial de URL/body é feito depois da geração para simular
masking — todas as verificações de mascaramento leem exatamente o que o
PlaywrightAdapter/result.json/HTML já produziram.

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

_STARTED_AT = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 28, 12, 0, 30, tzinfo=timezone.utc)

_USERNAME = "test-user"
# Valores fictícios, exclusivos deste teste — nunca uma credencial real.
_SECRET_VARIABLE_VALUE = "SECRET_TEST_VALUE_123456"  # Cenário 1: {{password}}
_SECRET_LITERAL_VALUE = "SECRET_LITERAL_TEST_123456"  # Cenário 2: literal cru
_MASKED_VARIABLE_SECRET = mask_secret(_SECRET_VARIABLE_VALUE)

_ENVIRONMENT_FOR_VARIABLE = PostmanEnvironment(
    name="QA",
    variables=(
        EnvironmentVariable(key="password", value=_SECRET_VARIABLE_VALUE, is_secret=True, enabled=True),
    ),
)
# Mesmo Environment, mas para o cenário do literal: declara o MESMO valor
# usado como literal na Collection como secreto — usado só para responder
# "existe algum mecanismo que reconheça esse valor por coincidência de
# valor, mesmo sem {{}}?" (ver teste dedicado abaixo).
_ENVIRONMENT_MATCHING_THE_LITERAL = PostmanEnvironment(
    name="QA",
    variables=(
        EnvironmentVariable(key="password", value=_SECRET_LITERAL_VALUE, is_secret=True, enabled=True),
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
_LITERAL_REQUEST = _login_request(_SECRET_LITERAL_VALUE)


def _analyzed(request: dict):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Literal Secret E2E",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "Login", "id": "r1", **request}],
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
# ============================================================================


def test_cenario1_geracao_variavel_nunca_expoe_o_valor_e_preserva_a_request(tmp_path):
    generated = _generated_endpoint(_VARIABLE_REQUEST, _ENVIRONMENT_FOR_VARIABLE)
    ast.parse(generated.content)  # ETAPA de validade: nunca aceitar .py inválido como "proteção"

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario1_gen", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # 1. O valor real não aparece no .py gerado.
    assert _SECRET_VARIABLE_VALUE not in content

    # 2. A estrutura do request permanece funcional: a chave "password"
    # continua no body, nunca removida/"" /"****" hardcoded — só o VALOR
    # foi deferido para uma variável resolvida em runtime.
    assert '"password": password,' in content
    assert '"password": "",' not in content
    assert '"password": "****",' not in content
    assert "AQO_PASSWORD" in content
    assert 'os.environ.get("AQO_PASSWORD")' in content
    assert "AQO_PASSWORD" in generated.required_environment_variables

    # 8. O campo username permanece intacto.
    assert f'"username": "{_USERNAME}",' in content


def test_cenario1_execucao_real_entrega_o_valor_e_mascara_a_jusante(tmp_path, monkeypatch):
    server = PostmanTestServer()
    try:
        server.set_route(
            "/login", method="PUT", status=200, body={"token": "fake-jwt-not-a-real-secret"}
        )

        generated = _generated_endpoint(_VARIABLE_REQUEST, _ENVIRONMENT_FOR_VARIABLE)
        assert _SECRET_VARIABLE_VALUE not in generated.content  # pré-condição

        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario1_e2e", generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        # Mecanismo real e já existente: a variável chega em runtime via
        # variável de ambiente do SO — nunca uma infraestrutura nova.
        monkeypatch.setenv("AQO_PASSWORD", _SECRET_VARIABLE_VALUE)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        # known_secret_values populado explicitamente aqui simula o que
        # run_command.py já faz em produção a partir de
        # EnvironmentVariable.is_secret.
        result = adapter.run(
            tests_path=str(suite_dir),
            timeout_seconds=90.0,
            known_secret_values=(_SECRET_VARIABLE_VALUE,),
        )

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True

        # 4. O servidor recebe o valor REAL (prova de que a proteção na
        # geração não quebrou o funcionamento em runtime).
        assert len(server.received_bodies) == 1
        assert server.received_bodies[0] == {"username": _USERNAME, "password": _SECRET_VARIABLE_VALUE}

        # 3. O Playwright recebeu o valor em runtime — confirmado pela
        # HttpTransaction capturada (o corpo REALMENTE enviado).
        assert len(result.http_transactions) == 1
        transaction = result.http_transactions[0]
        assert transaction.test_id
        assert transaction.method == "PUT"
        assert transaction.request_body is not None
        # Evidência pós-execução já chega mascarada do PlaywrightAdapter.
        assert _SECRET_VARIABLE_VALUE not in transaction.request_body
        assert _MASKED_VARIABLE_SECRET in transaction.request_body
        assert _USERNAME in transaction.request_body  # 8. isolamento

        raw_payload, record, report, html = _persist_read_report_html(
            result, tmp_path / "run_cenario1"
        )

        # 5. result.json não contém o valor real.
        assert _SECRET_VARIABLE_VALUE not in json.dumps(raw_payload)
        # 7. A forma mascarada aparece nas evidências.
        assert _MASKED_VARIABLE_SECRET in json.dumps(raw_payload)

        # 6. HTML não contém o valor real; forma mascarada presente.
        assert _SECRET_VARIABLE_VALUE not in html
        assert _MASKED_VARIABLE_SECRET in html

        # 8. username permanece correto em toda a cadeia.
        assert _USERNAME in json.dumps(raw_payload)
        assert _USERNAME in html
    finally:
        server.shutdown()


# ============================================================================
# CENÁRIO 2 — secret colocado diretamente como literal na Collection
# ============================================================================


def test_cenario2_geracao_literal_sem_environment(tmp_path):
    # Sem nenhum Environment envolvido — o caso mais simples e mais comum
    # de um literal cru na Collection.
    generated = _generated_endpoint(_LITERAL_REQUEST, environment=None)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_gen_sem_env", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    # 1. ACHADO: o literal aparece no .py gerado — a arquitetura atual não
    # protege um valor sensível literal no body, mesmo sendo
    # estruturalmente idêntico ao Cenário 1 (mesma chave "password", mesmo
    # tipo de dado). Registrado explicitamente como comportamento atual —
    # nunca escondido nem mascarado dentro deste teste.
    literal_exposed = _SECRET_LITERAL_VALUE in content
    assert literal_exposed, (
        "ACHADO deixou de se reproduzir: SECRET_LITERAL_TEST_123456 não "
        "aparece mais literalmente no .py gerado — se isso for uma "
        "correção deliberada, atualize este teste de caracterização "
        "conscientemente (troque para assert not literal_exposed)."
    )
    assert f'"password": "{_SECRET_LITERAL_VALUE}",' in content
    assert f'"username": "{_USERNAME}",' in content  # username nunca afetado


def test_cenario2_geracao_literal_mesmo_com_environment_declarando_o_mesmo_valor_como_secret(
    tmp_path,
):
    # 6. Existe algum mecanismo atual que reconheça esse valor como
    # secret, mesmo sem {{}}? Para HEADERS existe (_matches_known_secret,
    # correspondência por VALOR) — este teste verifica se o MESMO
    # mecanismo se aplica a um literal de BODY. Nenhum comportamento novo
    # é inventado aqui: só se observa o que o código real já faz.
    generated = _generated_endpoint(_LITERAL_REQUEST, _ENVIRONMENT_MATCHING_THE_LITERAL)
    ast.parse(generated.content)

    suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_gen_com_env", generated)
    content = _endpoint_file_content(suite_dir)
    ast.parse(content)

    literal_exposed = _SECRET_LITERAL_VALUE in content
    assert literal_exposed, (
        "ACHADO deixou de se reproduzir: mesmo com um Environment "
        "declarando o mesmo valor como secreto, o literal de BODY não "
        "aparece mais no .py — atualize este teste conscientemente se "
        "isso for uma correção deliberada."
    )


def test_cenario2_execucao_real_literal_chega_ao_servidor_sem_protecao_a_jusante(
    tmp_path, monkeypatch
):
    server = PostmanTestServer()
    try:
        server.set_route(
            "/login", method="PUT", status=200, body={"token": "fake-jwt-not-a-real-secret"}
        )

        generated = _generated_endpoint(_LITERAL_REQUEST, environment=None)
        assert _SECRET_LITERAL_VALUE in generated.content  # pré-condição (mesmo achado da geração)

        suite_dir = _write_single_endpoint_suite(tmp_path, "cenario2_e2e", generated)

        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", server.base_url)
        adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
        # known_secret_values=() DE PROPÓSITO, nunca omitido por engano:
        # em produção, nada popularia isto com um valor que só existe
        # como literal cru numa Collection — o mecanismo real de
        # known_secret_values vem de EnvironmentVariable.is_secret, que
        # este literal nunca teve (não há {{}} referenciando nada).
        result = adapter.run(tests_path=str(suite_dir), timeout_seconds=90.0)

        assert result.infrastructure_failure is None, (
            f"execução falhou por infraestrutura: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
        assert result.success is True

        # 2. O literal chega ao servidor durante a execução (a request
        # nunca deixou de funcionar — o problema é exclusivamente de
        # segurança, nunca funcional).
        assert len(server.received_bodies) == 1
        assert server.received_bodies[0] == {"username": _USERNAME, "password": _SECRET_LITERAL_VALUE}

        # 3. ACHADO: o literal aparece em http_transactions em claro —
        # nada no pipeline sabia que este valor precisava ser mascarado.
        assert len(result.http_transactions) == 1
        transaction = result.http_transactions[0]
        assert transaction.request_body is not None
        assert _SECRET_LITERAL_VALUE in transaction.request_body
        assert _USERNAME in transaction.request_body  # username nunca afetado

        raw_payload, record, report, html = _persist_read_report_html(
            result, tmp_path / "run_cenario2"
        )

        # 4. ACHADO: aparece em claro no result.json.
        assert _SECRET_LITERAL_VALUE in json.dumps(raw_payload)

        # 5. ACHADO: aparece em claro no HTML.
        assert _SECRET_LITERAL_VALUE in html

        # username, ao lado, nunca é afetado por esse achado.
        assert _USERNAME in json.dumps(raw_payload)
        assert _USERNAME in html
    finally:
        server.shutdown()
