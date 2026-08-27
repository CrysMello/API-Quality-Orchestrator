"""Teste de caracterização permanente — cadeia completa, ponta a ponta,
EXCLUSIVAMENTE do fluxo Playwright (nunca Postman/Newman), com execução
REAL (Playwright de verdade, pytest de verdade, servidor HTTP real em
localhost, nunca fake_pytest.py, nunca um gerador mockado):

    TestStrategy -> PlaywrightEndpointTestGenerator -> código gerado
    -> execução real -> AssertionResult -> ExecutionResult -> result.json
    -> JsonExecutionResultReader -> ReportEngine -> HTML

Cobre 3 assertions determinísticas por endpoint (status_code, valor exato
de campo, presença de campo) para provar que nenhuma delas se perde nem é
alterada em nenhuma fronteira do pipeline. Nenhum mock substitui as
camadas sob teste; a única infraestrutura externa usada é o servidor HTTP
local já existente em tests/postman_test_server.py (fixture
`postman_test_server`) e o repositório de persistência (grava em
tmp_path real, mesmo padrão de tests/unit/test_http_evidence_round_trip.py).

P2.2 (assertions independentes): este arquivo originalmente documentou um
achado real — quando o schema também aciona a validação genérica
"json_schema" (sempre gerada junto de qualquer AssertionType.SCHEMA), uma
falha nela interrompia o teste ANTES da checagem mais específica
"expected_value:id" rodar, e esta nunca chegava a gerar seu próprio
AssertionResult. Isso foi corrigido no bloco P2.2 (playwright_endpoint_
test_generator.py): cada categoria de assertion agora é avaliada de forma
independente (falha registrada, execução continua) e o teste só é
marcado como reprovado ao final, depois de todas terem rodado — nunca
antes. O cenário B abaixo valida exatamente essa correção.

Documenta o comportamento ATUAL — se quebrar por uma mudança deliberada
(schema bump, mudança no gerador), atualize-o conscientemente (ver
tests/characterization/README.md).
"""

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
    ExecutionContext,
    ExecutionMode,
    ExecutionResultLocation,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    DefaultPlaywrightTestSuiteBuilder,
    PlaywrightEndpointTestGenerator,
)
from api_quality_agent.parsers import PostmanCollectionParser
from api_quality_agent.reporting import ReportEngine, render_execution_report_html

_STARTED_AT = datetime(2026, 8, 27, 9, 0, 0, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 8, 27, 9, 0, 30, tzinfo=timezone.utc)

# Resposta real que o servidor HTTP local devolve nos dois cenários —
# NUNCA muda entre A e B; só a expectativa declarada na TestStrategy muda
# (ver CENÁRIO B), exatamente como pedido ("altere APENAS uma
# expectativa").
_RESPONSE_BODY = {"id": 123, "name": "Crys", "active": True}


class _RealFileRepository:
    # Mesmo padrão já usado em tests/unit/test_persist_execution_result_use_case.py
    # e tests/unit/test_http_evidence_round_trip.py — a única fronteira
    # externa mockada nesta cadeia é o repositório de persistência (grava
    # de verdade em tmp_path).
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
                    "name": "Customers API",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "Customer", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _three_assertions(*, expected_id: int) -> tuple[AssertionDefinition, ...]:
    # As "3 assertions determinísticas" do enunciado, representadas com os
    # modelos REAIS do projeto — nenhum modelo novo, nenhum mecanismo novo:
    #
    #   1. status_code == 200            -> AssertionType.STATUS_CODE
    #   2. body.id == expected_id        -> AssertionType.SCHEMA (properties.id.const)
    #   3. body.name existe              -> AssertionType.SCHEMA (required: ["name"])
    #
    # ACHADO DE MODELAGEM #1 (documentado, não uma correção): o modelo real
    # deste projeto representa "valor exato de um campo" e "presença de um
    # campo" como DUAS FACETAS DE UM ÚNICO AssertionDefinition(SCHEMA, ...)
    # — nunca um AssertionDefinition por checagem de campo. Isso é
    # deliberado e já é como TestStrategyEngine._build_schema_related_assertions
    # monta uma estratégia real a partir de um schema de resposta (uma
    # única entrada SCHEMA cobre required/const/enum/tipos juntos).
    # `_find_schema_assertion` (playwright_endpoint_test_generator.py) usa
    # next(...) e só enxerga a PRIMEIRA entrada AssertionType.SCHEMA em
    # strategy.assertions — uma segunda entrada SCHEMA seria silenciosamente
    # ignorada. Por isso as assertions 2 e 3 são combinadas num único
    # schema aqui, exatamente como a produção real faz — nunca duas
    # entradas SCHEMA separadas (isso reproduziria uma perda de informação
    # que não reflete o uso real do sistema).
    #
    # AssertionType.VALID_JSON_BODY também está presente: é a MESMA peça de
    # infraestrutura que TestStrategyEngine sempre adiciona quando a
    # resposta documentada tem corpo JSON (ver test_strategy_engine.py,
    # `has_json_content`) — habilita o parse do body para as checagens 2/3,
    # mas não é, ela própria, uma das "3 assertions" do enunciado, e (
    # confirmado abaixo) não produz nenhum AssertionResult próprio.
    #
    # NOTA (histórico, corrigido no P2.2): declarar QUALQUER
    # AssertionType.SCHEMA com um dict sempre também gera, sem nenhuma
    # forma de desligar, uma validação estrutural completa via
    # jsonschema.validate() (categoria "json_schema" —
    # _resolve_json_schema_assertion não tem nenhuma condição além de
    # "existe um SCHEMA assertion com dict"), que roda ANTES da checagem
    # individual de "expected_value" na ordem do código gerado
    # (_generate_positive_success_test: status -> content_type -> body_json
    # -> required_fields -> field_types -> json_schema -> expected_values).
    # Como "const" é uma palavra-chave nativa do JSON Schema, uma violação
    # de const É capturada pelo jsonschema.validate() também. Antes do
    # P2.2, isso fazia a checagem "expected_value:id" nunca chegar a
    # executar (o pytest.fail() de "json_schema" interrompia o teste
    # primeiro). Agora cada categoria é avaliada de forma independente —
    # ambas geram seu próprio AssertionResult, sem uma impedir a outra. O
    # teste abaixo comprova isso com execução real, não só leitura do
    # código.
    return (
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
        AssertionDefinition(
            assertion_type=AssertionType.SCHEMA,
            description="O corpo da resposta deve validar contra o schema esperado.",
            expected_value={
                "type": "object",
                "properties": {
                    "id": {"const": expected_id},
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
            origin="contract",
        ),
    )


def _build_real_suite(tmp_path: Path, *, expected_id: int, name: str):
    # Gerador REAL de ponta a ponta: PlaywrightEndpointTestGenerator produz
    # o arquivo do endpoint, DefaultPlaywrightTestSuiteBuilder monta o
    # conftest.py (captura de HTTP transaction/assertion/trace) e o
    # manifesto — exatamente a mesma suíte física que
    # GeneratePlaywrightTestSuiteUseCase persistiria em produção.
    strategy_source, normalized_request = _analyzed(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/customers/123"}}
    )
    strategy = TestStrategy(
        endpoint_source=strategy_source.source,
        assertions=_three_assertions(expected_id=expected_id),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    # === ETAPA 1 — TestStrategy possui as 3 assertions antes da geração ===
    assert len(strategy.assertions) == 3
    status_assertion = strategy.assertions[0]
    schema_assertion = strategy.assertions[2]
    assert status_assertion.assertion_type is AssertionType.STATUS_CODE
    assert status_assertion.expected_value == 200
    assert schema_assertion.assertion_type is AssertionType.SCHEMA
    assert schema_assertion.expected_value["properties"]["id"]["const"] == expected_id
    assert schema_assertion.expected_value["required"] == ["name"]

    generated_endpoint = PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy, normalized_request
    )

    # === ETAPA 2 — Generator transformou as 3 assertions em código real ===
    assert "@pytest.mark.skip" not in generated_endpoint.content
    content = generated_endpoint.content
    # 1. status_code == 200 — semanticamente, não só "assert" no texto.
    assert "assert response.status == 200" in content
    # 2. body.id == expected_id — checagem de VALOR EXATO do campo "id"
    #    (_get_nested_value + comparação != expected_id, nunca só a palavra
    #    "assert"; ver _render_expected_value_check_lines).
    assert "_value = _get_nested_value(body, ('id',))" in content
    assert f"if _value != {expected_id}:" in content
    # 3. body.name existe — checagem de PRESENÇA do campo "name" (helper
    #    dedicado, nunca comparação de valor; ver
    #    _resolve_required_fields_assertion).
    assert "_assert_required_field_present(body, ('name',)," in content

    execution_context = ExecutionContext.create(
        mode=ExecutionMode.OFFLINE,
        source="playwright-generation",
        workspace_id=None,
        collection_id="col-1",
        collection_name="Customers API",
        id_factory=lambda: f"exec-{name}",
    )
    suite = DefaultPlaywrightTestSuiteBuilder().build([generated_endpoint], execution_context)

    suite_dir = tmp_path / f"suite_{name}"
    for generated_file in suite.files:
        file_path = suite_dir / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(generated_file.content, encoding="utf-8")
    return suite_dir


def _run_full_pipeline(suite_dir: Path, run_dir: Path, base_url: str, monkeypatch):
    # === ETAPA 3 — Execução real (Playwright real, pytest real) ===========
    # PLAYWRIGHT_BASE_URL (mesmo mecanismo de _render_conftest) aponta o
    # api_context real para o servidor HTTP local, sem precisar regenerar a
    # suíte com host/porta (só conhecidos depois que o servidor sobe).
    monkeypatch.setenv("PLAYWRIGHT_BASE_URL", base_url)
    adapter = PlaywrightAdapter(pytest_executable=sys.executable, command_prefix=("-m", "pytest"))
    result = adapter.run(tests_path=str(suite_dir), timeout_seconds=90.0)

    assert result.infrastructure_failure is None, (
        f"execução falhou por infraestrutura, não pela assertion: "
        f"{result.stdout[-2000:]} {result.stderr[-2000:]}"
    )
    # Confirma que a chamada HTTP realmente aconteceu (não é só "o teste
    # não quebrou"): pelo menos uma HttpTransaction real foi capturada, com
    # o corpo de resposta que o servidor local de fato devolveu.
    assert len(result.http_transactions) == 1
    transaction = result.http_transactions[0]
    assert transaction.response_status == 200
    assert json.loads(transaction.response_body) == _RESPONSE_BODY

    # === ETAPA 4 — ExecutionResult real ====================================
    # Desde o P2.2, as 5 categorias que este schema aciona são sempre
    # avaliadas de forma independente nos dois cenários (A e B) — a
    # composição exata (nomes/status) é verificada em cada teste de
    # cenário, não aqui.
    test_ids = {assertion.test_id for assertion in result.assertion_results}
    assert len(test_ids) == 1 and next(iter(test_ids))  # mesmo test_id, não vazio

    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))
    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="Customers API",
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
    return result, raw_payload, record, report, html


def _assertion_by_name(assertions, name: str):
    return next(a for a in assertions if a.name == name)


# === Cenário A — as 3 assertions satisfeitas ================================


def test_scenario_a_three_satisfied_assertions_survive_the_real_pipeline_to_html(
    tmp_path, postman_test_server, monkeypatch
):
    postman_test_server.set_route("/customers/123", method="GET", status=200, body=_RESPONSE_BODY)
    suite_dir = _build_real_suite(tmp_path, expected_id=123, name="a")

    result, raw_payload, record, report, html = _run_full_pipeline(
        suite_dir, tmp_path / "run_a", postman_test_server.base_url, monkeypatch
    )

    # --- ETAPA 4: ExecutionResult ---------------------------------------------
    # Quando as 3 assertions do enunciado PASSAM, todas as 5 categorias
    # reais que o schema aciona (status, presença, tipo, json_schema E a
    # checagem específica de valor) chegam a executar e são registradas —
    # nenhuma é suprimida pelas outras neste caminho. As 3 que o enunciado
    # pediu (status_code, expected_value:id, required_field:name) estão
    # todas presentes e corretas; field_type:name e json_schema são
    # categorias adicionais que o mesmo schema aciona.
    assert result.success is True
    assert result.test_failures == ()
    expected_names = {
        "HTTP status",
        "required_field:name",
        "field_type:name",
        "json_schema",
        "expected_value:id",
    }
    assert {a.name for a in result.assertion_results} == expected_names
    assert all(a.status == "PASSED" for a in result.assertion_results)
    status_result = _assertion_by_name(result.assertion_results, "HTTP status")
    id_result = _assertion_by_name(result.assertion_results, "expected_value:id")
    name_result = _assertion_by_name(result.assertion_results, "required_field:name")
    assert (status_result.expected, status_result.actual) == (200, 200)
    assert (id_result.expected, id_result.actual) == (123, 123)
    assert (name_result.expected, name_result.actual) == ("presente", "presente")
    test_id = status_result.test_id
    assert all(a.test_id == test_id for a in result.assertion_results)

    # --- ETAPA 5: result.json --------------------------------------------------
    assert raw_payload["success"] is True
    persisted_by_name = {a["name"]: a for a in raw_payload["assertion_results"]}
    assert set(persisted_by_name.keys()) == expected_names
    for name, expected, actual in (
        ("HTTP status", 200, 200),
        ("expected_value:id", 123, 123),
        ("required_field:name", "presente", "presente"),
    ):
        assert persisted_by_name[name]["status"] == "PASSED"
        assert persisted_by_name[name]["expected"] == expected
        assert persisted_by_name[name]["actual"] == actual
        assert persisted_by_name[name]["test_id"] == test_id

    # --- ETAPA 6: releitura (JsonExecutionResultReader) -------------------------
    assert record.success is True
    reread_by_name = {a.name: a for a in record.assertion_results}
    assert set(reread_by_name.keys()) == expected_names
    for name in expected_names:
        assert reread_by_name[name].status == "PASSED"
        assert reread_by_name[name].expected == persisted_by_name[name]["expected"]
        assert reread_by_name[name].actual == persisted_by_name[name]["actual"]
        assert reread_by_name[name].test_id == test_id

    # --- ETAPA 7: ReportEngine ---------------------------------------------------
    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert test.test_id == test_id
    assert {a.name for a in test.assertions} == expected_names
    assert all(a.status == "PASSED" for a in test.assertions)

    # --- ETAPA 8: HTML -----------------------------------------------------------
    assert 'class="status status-passed"' in html
    assert 'class="status status-failed"' not in html
    assert html.count('class="assertion assertion-passed"') == len(expected_names)
    assert html.count('class="assertion assertion-failed"') == 0
    assert "Ver Trace" not in html
    assert "Expected" in html and "Actual" in html


# === Cenário B — apenas UMA expectativa não satisfeita =======================


def test_scenario_b_one_unsatisfied_assertion_survives_the_real_pipeline_to_html(
    tmp_path, postman_test_server, monkeypatch
):
    # Mesma resposta real (id=123) do cenário A — só a expectativa
    # declarada muda (id==999). Nunca um cenário de negócio negativo: é a
    # MESMA API respondendo do mesmo jeito, só uma das 3 assertions
    # declaradas está errada de propósito.
    postman_test_server.set_route("/customers/123", method="GET", status=200, body=_RESPONSE_BODY)
    suite_dir = _build_real_suite(tmp_path, expected_id=999, name="b")

    result, raw_payload, record, report, html = _run_full_pipeline(
        suite_dir, tmp_path / "run_b", postman_test_server.base_url, monkeypatch
    )

    # --- ETAPA 4: ExecutionResult ---------------------------------------------
    # P2.2 (assertions independentes): antes desta correção, o pytest.fail()
    # da categoria genérica "json_schema" disparava ANTES da linha
    # "_value = _get_nested_value(body, ('id',))" da categoria específica
    # "expected_value:id" ser alcançada, e essa nunca chegava a gerar seu
    # próprio AssertionResult. Agora cada categoria é avaliada de forma
    # independente (falha registrada, execução continua) e só ao final,
    # depois de TODAS terem rodado, o teste é marcado como reprovado uma
    # única vez — por isso as 5 categorias que este schema aciona aparecem
    # todas aqui, com "json_schema" e "expected_value:id" cada uma gerando
    # seu próprio resultado FAILED, sem uma impedir a outra.
    assert result.success is False
    assert len(result.test_failures) == 1
    # A mensagem agregada de falha do pytest concatena a mensagem de cada
    # categoria reprovada — "id" e o valor esperado (999) aparecem (nunca
    # escondidos); o valor REAL recebido (123) nunca aparece na mensagem de
    # falha do pytest em si (regra 6 preservada), mas agora sobrevive
    # corretamente no AssertionResult "expected_value:id" (ver abaixo).
    assert "id" in result.test_failures[0].error_message
    assert "999" in result.test_failures[0].error_message

    expected_names = {
        "HTTP status",
        "required_field:name",
        "field_type:name",
        "json_schema",
        "expected_value:id",
    }
    assert {a.name for a in result.assertion_results} == expected_names
    status_result = _assertion_by_name(result.assertion_results, "HTTP status")
    name_result = _assertion_by_name(result.assertion_results, "required_field:name")
    type_result = _assertion_by_name(result.assertion_results, "field_type:name")
    schema_result = _assertion_by_name(result.assertion_results, "json_schema")
    id_result = _assertion_by_name(result.assertion_results, "expected_value:id")
    assert status_result.status == "PASSED"
    assert name_result.status == "PASSED"
    assert type_result.status == "PASSED"
    assert schema_result.status == "FAILED"
    assert schema_result.expected == "válido conforme schema documentado"
    # O json_schema (categoria genérica) continua sem o valor real recebido
    # — só uma descrição textual do valor esperado (999).
    assert "999" in schema_result.actual
    assert "123" not in schema_result.actual
    # A correção do P2.2: expected_value:id AGORA gera seu próprio
    # resultado, com expected/actual estruturados e corretos (999/123) —
    # nunca mais suprimido pela falha de json_schema.
    assert (id_result.status, id_result.expected, id_result.actual) == ("FAILED", 999, 123)
    test_id = status_result.test_id
    assert schema_result.test_id == test_id and id_result.test_id == test_id
    assert result.test_failures[0].test_name == test_id

    # --- ETAPA 5: result.json --------------------------------------------------
    assert raw_payload["success"] is False
    assert len(raw_payload["test_failures"]) == 1
    persisted_names = {a["name"] for a in raw_payload["assertion_results"]}
    assert persisted_names == expected_names
    persisted_id = next(a for a in raw_payload["assertion_results"] if a["name"] == "expected_value:id")
    assert persisted_id["status"] == "FAILED"
    assert persisted_id["expected"] == 999
    assert persisted_id["actual"] == 123
    assert persisted_id["test_id"] == test_id

    # --- ETAPA 6: releitura -------------------------------------------------------
    assert record.success is False
    assert len(record.test_failures) == 1
    reread_names = {a.name for a in record.assertion_results}
    assert reread_names == persisted_names
    reread_id = next(a for a in record.assertion_results if a.name == "expected_value:id")
    assert reread_id.status == "FAILED"
    assert reread_id.expected == 999
    assert reread_id.actual == 123

    # --- ETAPA 7: ReportEngine (correlação preservada) --------------------------
    assert len(report.execution.tests) == 1
    test = report.execution.tests[0]
    assert test.test_id == test_id
    assert {a.name for a in test.assertions} == persisted_names
    failed_id_in_report = next(a for a in test.assertions if a.name == "expected_value:id")
    assert failed_id_in_report.status == "FAILED"
    assert failed_id_in_report.expected == 999
    assert failed_id_in_report.actual == 123
    assert sum(1 for a in test.assertions if a.status == "FAILED") == 2  # json_schema + expected_value:id
    assert sum(1 for a in test.assertions if a.status == "PASSED") == 3

    # --- ETAPA 8: HTML -------------------------------------------------------------
    assert 'class="status status-failed"' in html
    assert 'class="status status-passed"' not in html
    assert html.count('class="assertion assertion-passed"') == 3
    assert html.count('class="assertion assertion-failed"') == 2
    assert "999" in html  # valor esperado aparece
    assert "123" in html  # valor real recebido agora aparece (via expected_value:id)

    # Trace-em-falha (P1.3) é automático no conftest.py gerado — um teste
    # que falhou de verdade gera um trace real, fora do escopo central
    # deste teste mas confirmado aqui por completude (nunca "Ver Trace"
    # indevido, já coberto no cenário A; aqui o oposto: nunca ausente
    # quando deveria existir).
    assert "Ver Trace" in html
