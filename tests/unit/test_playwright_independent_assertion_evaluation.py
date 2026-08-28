"""P2.2 do bloco Playwright: assertions avaliadas de forma independente
dentro do mesmo teste gerado.

Antes desta correção, a primeira assertion reprovada (`pytest.fail()`/
`raise` imediato) interrompia a execução do teste, impedindo que as
demais assertions declaradas fossem avaliadas — em particular, uma falha
de "json_schema" (categoria genérica, sempre gerada junto de qualquer
AssertionType.SCHEMA) podia impedir "expected_value:id" (mais específica)
de sequer rodar. Agora cada categoria registra seu próprio resultado e
continua; o teste só é marcado como reprovado ao final, uma única vez,
depois de todas terem sido avaliadas.

Mesmo padrão de execução já usado por
tests/unit/test_playwright_false_positive_prevention.py: o texto EXATO
que sai do gerador real (PlaywrightEndpointTestGenerator), executado via
exec() contra um api_context falso (só a fatia da API realmente usada) —
nunca uma cópia reescrita à mão.
"""

import ast
import json

import pytest

from api_quality_agent.domain.models import AssertionDefinition, AssertionType, TestStrategy
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import PlaywrightEndpointTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser

_GET_CUSTOMER = {"request": {"method": "GET", "url": "https://api.exemplo.com/customers/123"}}
_RESPONSE_BODY = {"id": 123, "name": "Crys"}


def _status_assertion(status_code: int = 200) -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
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


def _assertions(*, expected_id: int) -> tuple[AssertionDefinition, ...]:
    # status_code == 200; body.name existe (required); body.id ==
    # expected_id (const) — mesma combinação (status/schema/required/
    # expected value) pedida pelos 4 testes deste arquivo.
    schema = {
        "type": "object",
        "properties": {"id": {"const": expected_id}, "name": {"type": "string"}},
        "required": ["name"],
    }
    return (_status_assertion(200), _valid_json_body_assertion(), _schema_assertion(schema))


def _analyzed(request: dict):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Collection",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "Customer", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _generate(assertions: tuple[AssertionDefinition, ...]):
    analysis, normalized_request = _analyzed(_GET_CUSTOMER)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None, body_text: str = ""):
        self.status = status
        self.headers = headers or {}
        self._body_text = body_text

    def text(self) -> str:
        return self._body_text


class _FakeApiContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def get(self, path, params=None, headers=None, data=None, multipart=None):
        return self._response


def _load_generated_test_function(content: str):
    ast.parse(content)  # nunca executa algo que nem é Python sintaticamente válido
    namespace: dict = {}
    exec(content, namespace)  # noqa: S102 - texto do próprio gerador, não input externo
    functions = [
        value for key, value in namespace.items() if key.startswith("test_") and callable(value)
    ]
    assert len(functions) == 1, "conteúdo gerado deveria definir exatamente uma função de teste"
    return functions[0]


def _run(generated, response: _FakeResponse) -> None:
    test_function = _load_generated_test_function(generated.content)
    test_function(_FakeApiContext(response))


def _recorded_entries(results_path) -> list[dict]:
    lines = results_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


_ALL_CATEGORIES = {
    "HTTP status",
    "required_field:name",
    "field_type:name",
    "json_schema",
    "expected_value:id",
}


# === Teste 1 — todas as assertions passam ====================================


def test_all_assertions_pass_and_each_produces_its_own_passed_result(tmp_path, monkeypatch):
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))
    generated = _generate(_assertions(expected_id=123))

    _run(generated, _FakeResponse(status=200, body_text=json.dumps(_RESPONSE_BODY)))  # nunca levanta

    by_name = {entry["name"]: entry for entry in _recorded_entries(results_path)}
    assert set(by_name.keys()) == _ALL_CATEGORIES
    assert all(entry["status"] == "PASSED" for entry in by_name.values())
    assert by_name["HTTP status"]["expected"] == 200 and by_name["HTTP status"]["actual"] == 200
    assert by_name["expected_value:id"]["expected"] == 123
    assert by_name["expected_value:id"]["actual"] == 123
    assert by_name["required_field:name"]["expected"] == "presente"


# === Teste 2 — uma assertion falha; as demais continuam sendo avaliadas =====


def test_one_failing_assertion_does_not_stop_the_others(tmp_path, monkeypatch):
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))
    # response.id = 123 (real); expected id = 999 (declarado errado de
    # propósito, mesmo critério do enunciado).
    generated = _generate(_assertions(expected_id=999))

    with pytest.raises(pytest.fail.Exception):
        _run(generated, _FakeResponse(status=200, body_text=json.dumps(_RESPONSE_BODY)))

    by_name = {entry["name"]: entry for entry in _recorded_entries(results_path)}
    # A assertion específica existe, com status/expected/actual corretos.
    assert by_name["expected_value:id"]["status"] == "FAILED"
    assert by_name["expected_value:id"]["expected"] == 999
    assert by_name["expected_value:id"]["actual"] == 123
    # As demais assertions continuam sendo avaliadas (nunca puladas por
    # causa da falha de "expected_value:id").
    assert set(by_name.keys()) == _ALL_CATEGORIES
    assert by_name["HTTP status"]["status"] == "PASSED"
    assert by_name["required_field:name"]["status"] == "PASSED"
    assert by_name["field_type:name"]["status"] == "PASSED"


# === Teste 3 — JSON Schema (const) + expected value: ambos independentes ===


def test_json_schema_and_expected_value_each_produce_their_own_failed_result(
    tmp_path, monkeypatch
):
    # O mesmo "const: 999" aciona TANTO a validação genérica "json_schema"
    # (sempre gerada, sem opção de desligar) QUANTO a checagem específica
    # "expected_value:id" — este é exatamente o caso que antes fazia
    # "expected_value:id" nunca ser avaliada (json_schema falhava primeiro
    # e interrompia o teste).
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))
    generated = _generate(_assertions(expected_id=999))

    with pytest.raises(pytest.fail.Exception):
        _run(generated, _FakeResponse(status=200, body_text=json.dumps(_RESPONSE_BODY)))

    by_name = {entry["name"]: entry for entry in _recorded_entries(results_path)}
    assert by_name["json_schema"]["status"] == "FAILED"
    assert by_name["expected_value:id"]["status"] == "FAILED"
    assert by_name["expected_value:id"]["expected"] == 999
    assert by_name["expected_value:id"]["actual"] == 123
    # Nenhuma das duas impediu a outra de ser avaliada e registrada.
    assert "json_schema" in by_name
    assert "expected_value:id" in by_name


# === Teste 4 — isolamento: uma falha não altera as demais ===================


def test_a_failing_assertion_never_alters_the_result_of_the_following_ones(tmp_path, monkeypatch):
    # Roda o MESMO cenário duas vezes — uma com todas as expectativas
    # corretas, outra só com "id" errado — e confirma que as 3 categorias
    # não relacionadas a "id" (status, presença de "name", tipo de "name")
    # produzem exatamente o mesmo resultado nos dois casos: a presença de
    # uma falha em outro lugar nunca altera status/expected/actual delas.
    results_path_pass = tmp_path / "pass.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path_pass))
    generated_pass = _generate(_assertions(expected_id=123))
    _run(generated_pass, _FakeResponse(status=200, body_text=json.dumps(_RESPONSE_BODY)))
    passing = {e["name"]: e for e in _recorded_entries(results_path_pass)}

    results_path_fail = tmp_path / "fail.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path_fail))
    generated_fail = _generate(_assertions(expected_id=999))
    with pytest.raises(pytest.fail.Exception):
        _run(generated_fail, _FakeResponse(status=200, body_text=json.dumps(_RESPONSE_BODY)))
    failing = {e["name"]: e for e in _recorded_entries(results_path_fail)}

    for name in ("HTTP status", "required_field:name", "field_type:name"):
        assert passing[name]["status"] == failing[name]["status"] == "PASSED"
        assert passing[name]["expected"] == failing[name]["expected"]
        assert passing[name]["actual"] == failing[name]["actual"]

    # E a diferença fica isolada exatamente na assertion que de fato mudou.
    assert passing["expected_value:id"]["status"] == "PASSED"
    assert failing["expected_value:id"]["status"] == "FAILED"
