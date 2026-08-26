"""Parte 25 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
"Prevenção de Falsos Positivos" — etapa de ENDURECIMENTO das Partes 16-24,
nenhuma categoria funcional nova. Prova, executando o texto EXATO que sai
do gerador (nunca uma cópia divergente) contra um `api_context` falso, que
um cenário gerado FALHA quando a resposta simulada está incorreta — nunca
fica verde pelo motivo errado.

`_FakeApiContext`/`_FakeResponse` imitam só a fatia da API do Playwright
que o código gerado realmente usa (`.get`/`.post(path, params=, headers=,
data=, multipart=)` e `response.status`/`response.headers.get(...)`/
`response.text()`) — o suficiente para rodar a função de teste gerada como
uma chamada Python comum, sem rede nem processo do Playwright de verdade.
"""

import ast
import json

import pytest

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    ExecutionContext,
    ExecutionMode,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    AssertionPrecision,
    BROAD_STATUS_ASSERTION,
    DefaultPlaywrightTestSuiteBuilder,
    EXPECTED_STATUS_NOT_DEFINED,
    GeneratedEndpointTest,
    GeneratedScenarioQualityError,
    PlaywrightEndpointTestGenerator,
    assert_no_false_positive_smells,
)
from api_quality_agent.parsers import PostmanCollectionParser

_GET_USERS = {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}


def _status_assertion(status_code: int, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
        origin=origin,
    )


def _content_type_assertion(content_type: str) -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.CONTENT_TYPE,
        description=f"Content-Type da resposta deve conter '{content_type}'.",
        expected_value=content_type,
        origin="contract",
    )


def _valid_json_body_assertion() -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.VALID_JSON_BODY,
        description="O corpo da resposta deve ser um JSON válido.",
        expected_value=None,
        origin="contract",
    )


def _schema_assertion(schema: dict) -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.SCHEMA,
        description="O corpo da resposta deve validar contra o schema esperado.",
        expected_value=schema,
        origin="contract",
    )


def _analyzed(request: dict):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Collection",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": [{"name": "R1", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _generate(request: dict, assertions: tuple[AssertionDefinition, ...]) -> GeneratedEndpointTest:
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)


# --- api_context falso: só a fatia usada pelo código gerado -----------------


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None, body_text: str = ""):
        self.status = status
        self.headers = headers or {}
        self._body_text = body_text

    def text(self) -> str:
        return self._body_text


class _FakeApiContext:
    def __init__(self, response: _FakeResponse | None):
        self._response = response

    def get(self, path, params=None, headers=None, data=None, multipart=None):
        return self._response

    def post(self, path, params=None, headers=None, data=None, multipart=None):
        return self._response


def _load_generated_test_function(content: str):
    # Mesmo texto exato que vira o arquivo gerado — nunca uma cópia
    # reescrita à mão que possa divergir.
    ast.parse(content)  # nunca executa algo que nem é Python sintaticamente válido
    namespace: dict = {}
    exec(content, namespace)  # noqa: S102 - texto do próprio gerador, não input externo
    functions = [
        value for key, value in namespace.items() if key.startswith("test_") and callable(value)
    ]
    if len(functions) != 1:
        raise AssertionError("conteúdo gerado deveria definir exatamente uma função de teste")
    return functions[0]


def _run_generated_scenario(generated: GeneratedEndpointTest, response: _FakeResponse | None) -> None:
    test_function = _load_generated_test_function(generated.content)
    test_function(_FakeApiContext(response))


# --- Caso 1: 401 nunca passa só por pertencer a 4xx -------------------------


def test_case1_error_status_never_passes_just_because_it_belongs_to_4xx():
    # Contrato espera 422 (erro de campo obrigatório) — a API respondeu 401
    # (outro erro 4xx, mas NÃO o esperado). Nunca pode passar só porque os
    # dois "são 4xx".
    generated = _generate(_GET_USERS, (_status_assertion(422),))

    with pytest.raises(AssertionError):
        _run_generated_scenario(generated, _FakeResponse(status=401))


def test_case1_client_and_auth_error_codes_never_validate_each_other():
    # 401/403/404/415 nunca se substituem silenciosamente (critério de
    # aceite explícito da Parte 25).
    codes = (401, 403, 404, 415)
    for expected in codes:
        generated = _generate(_GET_USERS, (_status_assertion(expected),))
        for actual in codes:
            if actual == expected:
                continue
            with pytest.raises(AssertionError):
                _run_generated_scenario(generated, _FakeResponse(status=actual))


# --- Caso 2: JSON válido não implica cenário aprovado ------------------------


def test_case2_valid_json_error_body_never_passes_a_positive_scenario():
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    generated = _generate(
        _GET_USERS,
        (
            _status_assertion(200),
            _content_type_assertion("application/json"),
            _valid_json_body_assertion(),
            _schema_assertion(schema),
        ),
    )

    # Status e Content-Type batem (a falha nunca pode ser "só porque não é
    # JSON"): o corpo É um JSON válido, só que é um erro, não o recurso
    # esperado. _assert_required_field_present usa `assert` puro (Parte
    # 19) — AssertionError, não pytest.fail.Exception.
    with pytest.raises(AssertionError) as excinfo:
        _run_generated_scenario(
            generated,
            _FakeResponse(
                status=200,
                headers={"content-type": "application/json"},
                body_text=json.dumps({"error": "Internal server error"}),
            ),
        )
    assert "obrigat" in str(excinfo.value)


# --- Caso 3: schema válido, valor funcional incorreto ------------------------


def test_case3_schema_shaped_correctly_but_functional_value_is_wrong():
    # "extra" tem um $ref remoto (nunca buscado — Parte 21): a validação
    # JSON Schema INTEIRA é pulada para este endpoint, isolando a falha na
    # checagem de valor esperado (Parte 22) — nunca no schema estrutural.
    schema = {
        "type": "object",
        "properties": {
            "status": {"const": "active"},
            "extra": {"$ref": "https://schemas.exemplo.com/extra.json"},
        },
    }
    generated = _generate(
        _GET_USERS,
        (_status_assertion(200), _valid_json_body_assertion(), _schema_assertion(schema)),
    )
    assert "jsonschema.validate" not in generated.content  # confirma que foi pulada

    with pytest.raises(pytest.fail.Exception) as excinfo:
        _run_generated_scenario(
            generated,
            _FakeResponse(status=200, body_text=json.dumps({"status": "inactive"})),
        )
    assert "status" in str(excinfo.value)


# --- Caso 4: Content-Type incorreto com body aparentemente JSON -------------


def test_case4_wrong_content_type_fails_even_with_json_looking_body():
    generated = _generate(
        _GET_USERS, (_status_assertion(200), _content_type_assertion("application/json"))
    )

    with pytest.raises(AssertionError):
        _run_generated_scenario(
            generated,
            _FakeResponse(
                status=200,
                headers={"content-type": "text/html"},
                body_text=json.dumps({"id": "1"}),
            ),
        )


# --- Caso 5: campo obrigatório ausente, resto do schema válido -------------


def test_case5_missing_required_field_fails_even_when_the_rest_of_the_schema_is_valid():
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "email": {"type": "string"}},
        "required": ["id", "email"],
    }
    generated = _generate(
        _GET_USERS,
        (_status_assertion(200), _valid_json_body_assertion(), _schema_assertion(schema)),
    )

    # "email" está presente e com o tipo certo (resto do schema válido);
    # só "id" falta.
    with pytest.raises(AssertionError) as excinfo:
        _run_generated_scenario(
            generated,
            _FakeResponse(status=200, body_text=json.dumps({"email": "ana@exemplo.com"})),
        )
    assert "id" in str(excinfo.value)


# --- Caso 6: tipo incorreto, sem coerção ------------------------------------


def test_case6_wrong_type_fails_without_any_coercion():
    schema = {"type": "object", "properties": {"age": {"type": "integer"}}}
    generated = _generate(
        _GET_USERS,
        (_status_assertion(200), _valid_json_body_assertion(), _schema_assertion(schema)),
    )

    # "42" como STRING (aspas no JSON) — nunca convertido para int só
    # porque "parece" um número.
    with pytest.raises(AssertionError) as excinfo:
        _run_generated_scenario(
            generated, _FakeResponse(status=200, body_text=json.dumps({"age": "42"}))
        )
    assert "esperado integer" in str(excinfo.value)
    assert "recebido string" in str(excinfo.value)


# --- Caso 7: BROAD permanece claramente identificado -------------------------


def test_case7_broad_scenario_is_explicitly_classified_and_warned_never_silent():
    generated = _generate(_GET_USERS, ())  # nenhuma evidência de status

    classification = next(c for c in generated.assertion_classifications if c.assertion == "status")
    assert classification.precision is AssertionPrecision.BROAD
    assert "[BROAD]" in generated.content
    codes = {w.code for w in generated.warnings}
    assert codes == {EXPECTED_STATUS_NOT_DEFINED, BROAD_STATUS_ASSERTION}


def test_case7_broad_approximation_still_catches_the_one_thing_it_can():
    # BROAD não é um no-op: "assert response is not None" continua sendo
    # uma checagem real, só limitada — nunca finge validar o status.
    generated = _generate(_GET_USERS, ())

    with pytest.raises(AssertionError):
        _run_generated_scenario(generated, response=None)

    _run_generated_scenario(generated, _FakeResponse(status=200))  # nunca levanta


def test_case7_broad_never_marks_the_endpoint_as_complete_coverage():
    generated = _generate(_GET_USERS, ())
    context = ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="C")

    suite = DefaultPlaywrightTestSuiteBuilder().build([generated], context)
    manifest = json.loads(
        next(f for f in suite.files if f.relative_path == "generation-manifest.json").content
    )

    coverage = next(e["coverage"] for e in manifest["endpoints"] if e["endpoint"] == "GET /users")
    assert coverage != "complete"


# --- Guardas: padrões proibidos nunca aparecem no conteúdo gerado -----------


def test_guard_rejects_response_json_is_not_none_as_the_only_check():
    with pytest.raises(GeneratedScenarioQualityError):
        assert_no_false_positive_smells(
            "def test_x(api_context):\n"
            "    response = api_context.get('/x')\n"
            "    assert response.json() is not None\n"
        )


def test_guard_rejects_response_ok_as_a_status_substitute():
    with pytest.raises(GeneratedScenarioQualityError):
        assert_no_false_positive_smells(
            "def test_x(api_context):\n"
            "    response = api_context.get('/x')\n"
            "    assert response.ok\n"
        )


def test_guard_rejects_status_ranges():
    with pytest.raises(GeneratedScenarioQualityError):
        assert_no_false_positive_smells(
            "def test_x(api_context):\n"
            "    response = api_context.get('/x')\n"
            "    assert 200 <= response.status < 300\n"
        )


def test_guard_rejects_retry_loops():
    with pytest.raises(GeneratedScenarioQualityError):
        assert_no_false_positive_smells(
            "def test_x(api_context):\n"
            "    for attempt in range(3):\n"
            "        response = api_context.get('/x')\n"
        )


def test_guard_never_trips_on_real_generator_output():
    scenarios = (
        (_GET_USERS, ()),
        (_GET_USERS, (_status_assertion(200),)),
        (_GET_USERS, (_status_assertion(401),)),
        (
            _GET_USERS,
            (
                _status_assertion(200),
                _content_type_assertion("application/json"),
                _valid_json_body_assertion(),
                _schema_assertion(
                    {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    }
                ),
            ),
        ),
    )
    for request, assertions in scenarios:
        generated = _generate(request, assertions)
        assert_no_false_positive_smells(generated.content)  # nunca levanta


def test_guard_is_wired_into_build_before_persistence():
    rigged = GeneratedEndpointTest(
        endpoint_source="GET /pets",
        suggested_file_name="ignored.py",
        content="def test_x(api_context):\n    response = api_context.get('/x')\n    assert response.ok\n",
        scenario_names=("success",),
        warnings=(),
    )
    context = ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="C")

    with pytest.raises(GeneratedScenarioQualityError):
        DefaultPlaywrightTestSuiteBuilder().build([rigged], context)


def test_no_generated_scenario_across_16_to_24_uses_response_ok_or_json_is_not_none():
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "role": {"type": "string", "enum": ["admin", "user"]},
        },
        "required": ["id"],
    }
    generated = _generate(
        _GET_USERS,
        (
            _status_assertion(200),
            _content_type_assertion("application/json"),
            _valid_json_body_assertion(),
            _schema_assertion(schema),
        ),
    )

    assert "response.ok" not in generated.content
    assert "response.json()" not in generated.content
    assert "range(" not in generated.content


# --- Guarda: cenário parcial nunca vira cobertura completa -------------------


def test_partial_scenarios_never_report_complete_coverage():
    context = ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="C")

    # Warning presente (Content-Type ausente do request, header duplicado
    # etc.) -> parcial, mesmo com um cenário de verdade renderizado.
    duplicated_header_request = {
        "request": {
            "method": "GET",
            "url": "https://api.exemplo.com/dup",
            "header": [
                {"key": "X-Custom", "value": "first"},
                {"key": "X-Custom", "value": "second"},
            ],
        }
    }
    partial = _generate(duplicated_header_request, (_status_assertion(200),))
    assert partial.warnings != ()

    suite = DefaultPlaywrightTestSuiteBuilder().build([partial], context)
    manifest = json.loads(
        next(f for f in suite.files if f.relative_path == "generation-manifest.json").content
    )
    entry = next(e for e in manifest["endpoints"] if e["endpoint"] == "GET /dup")
    assert entry["coverage"] == "partial"


def test_fully_exact_scenario_is_the_only_case_marked_complete():
    context = ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="C")
    complete = _generate(_GET_USERS, (_status_assertion(200),))
    assert complete.warnings == ()

    suite = DefaultPlaywrightTestSuiteBuilder().build([complete], context)
    manifest = json.loads(
        next(f for f in suite.files if f.relative_path == "generation-manifest.json").content
    )
    entry = next(e for e in manifest["endpoints"] if e["endpoint"] == "GET /users")
    assert entry["coverage"] == "complete"


# --- Guarda: warnings nunca somem para "limpar" o resultado -----------------


# --- Guarda: instrumentação de AssertionResult (P1.1) nunca enfraquece a ----
# --- prevenção de falso positivo já provada acima --------------------------


def test_assertion_result_recording_never_swallows_or_weakens_a_real_failure(tmp_path, monkeypatch):
    # A mesma verificação do Caso 1 (401 nunca passa por engano quando o
    # contrato espera 422), agora com a gravação de AssertionResult ligada
    # (PLAYWRIGHT_ASSERTION_RESULTS_PATH setado) — prova que instrumentar a
    # asserção original (try/except ao redor do `assert` inalterado) nunca
    # relaxa, engole ou substitui o comportamento de falha já garantido.
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))

    generated = _generate(_GET_USERS, (_status_assertion(422),))
    assert_no_false_positive_smells(generated.content)  # conteúdo real, nunca um smell proibido

    with pytest.raises(AssertionError):
        _run_generated_scenario(generated, _FakeResponse(status=401))

    lines = results_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    recorded = json.loads(lines[0])
    assert recorded["name"] == "HTTP status"
    assert recorded["expected"] == 422
    assert recorded["actual"] == 401
    assert recorded["status"] == "FAILED"


def test_assertion_result_recording_never_swallows_or_weakens_a_real_pass(tmp_path, monkeypatch):
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))

    generated = _generate(_GET_USERS, (_status_assertion(200),))
    assert_no_false_positive_smells(generated.content)

    _run_generated_scenario(generated, _FakeResponse(status=200))  # nunca levanta

    lines = results_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    recorded = json.loads(lines[0])
    assert recorded["name"] == "HTTP status"
    assert recorded["expected"] == 200
    assert recorded["actual"] == 200
    assert recorded["status"] == "PASSED"


def test_content_type_recording_never_weakens_the_case4_failure(tmp_path, monkeypatch):
    # Mesmo cenário do Caso 4 (Content-Type incorreto com body aparentemente
    # JSON) — agora com a gravação ligada: prova que a complementação de
    # content_type nunca relaxa a falha já garantida.
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))
    generated = _generate(
        _GET_USERS, (_status_assertion(200), _content_type_assertion("application/json"))
    )
    assert_no_false_positive_smells(generated.content)

    with pytest.raises(AssertionError):
        _run_generated_scenario(
            generated,
            _FakeResponse(
                status=200,
                headers={"content-type": "text/html"},
                body_text=json.dumps({"id": "1"}),
            ),
        )

    recorded = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
    content_type_entry = next(e for e in recorded if e["name"] == "Content-Type")
    assert content_type_entry["status"] == "FAILED"
    assert content_type_entry["expected"] == "application/json"
    assert content_type_entry["actual"] == "text/html"


def test_json_schema_recording_never_weakens_the_case3_failure(tmp_path, monkeypatch):
    # Mesmo cenário do Caso 3 (schema com formato certo, valor funcional
    # errado) — a gravação de json_schema (já instrumentado antes desta
    # complementação) continua reportando corretamente o motivo real da
    # falha detectada pela Parte 22 (expected_values), sem nenhuma
    # interferência entre as duas categorias.
    # "extra" tem um $ref remoto (nunca buscado — Parte 21): a validação
    # JSON Schema INTEIRA é pulada para este endpoint (mesmo truque do
    # Caso 3 acima), isolando a falha na checagem de valor esperado (Parte
    # 22) — senão o próprio jsonschema.validate já rejeitaria o "const"
    # antes de expected_values ser avaliado.
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))
    schema = {
        "type": "object",
        "properties": {
            "status": {"const": "active"},
            "extra": {"$ref": "https://schemas.exemplo.com/extra.json"},
        },
    }
    generated = _generate(
        _GET_USERS,
        (_status_assertion(200), _valid_json_body_assertion(), _schema_assertion(schema)),
    )
    assert "jsonschema.validate" not in generated.content  # confirma que foi pulada
    assert_no_false_positive_smells(generated.content)

    with pytest.raises(pytest.fail.Exception):
        _run_generated_scenario(
            generated, _FakeResponse(status=200, body_text=json.dumps({"status": "inactive"}))
        )

    recorded = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
    expected_value_entry = next(e for e in recorded if e["name"] == "expected_value:status")
    assert expected_value_entry["status"] == "FAILED"
    assert expected_value_entry["expected"] == "active"
    assert expected_value_entry["actual"] == "inactive"


def test_broad_warnings_are_never_silently_removed():
    generated = _generate(_GET_USERS, ())

    # Regenerar o mesmo cenário repetidamente nunca faz os warnings
    # desaparecerem (nenhum estado escondido, nenhuma "limpeza").
    for _ in range(3):
        regenerated = _generate(_GET_USERS, ())
        codes = {w.code for w in regenerated.warnings}
        assert codes == {EXPECTED_STATUS_NOT_DEFINED, BROAD_STATUS_ASSERTION}

    context = ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="C")
    suite = DefaultPlaywrightTestSuiteBuilder().build([generated], context)
    manifest = json.loads(
        next(f for f in suite.files if f.relative_path == "generation-manifest.json").content
    )
    manifest_codes = {
        w["code"] for w in manifest["warnings"] if w["endpoint"] == "GET /users"
    }
    assert manifest_codes == {EXPECTED_STATUS_NOT_DEFINED, BROAD_STATUS_ASSERTION}
