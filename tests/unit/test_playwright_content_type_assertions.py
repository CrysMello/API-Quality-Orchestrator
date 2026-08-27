"""Parte 17 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
validação de Content-Type orientada por evidência já disponível em
strategy.assertions (AssertionType.CONTENT_TYPE) — case-insensitive no nome
do header, media type separado dos parâmetros (charset etc.), nunca
igualdade rígida da string completa, nunca exige o header quando não há
evidência (ex.: resposta 204 sem Content-Type documentado).
"""

import ast
import json

import pytest

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import PlaywrightEndpointTestGenerator
from api_quality_agent.generators.postman_test_generator import PostmanTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser

_GET_USERS = {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}


def _status_assertion(status_code: int, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
        origin=origin,
    )


def _content_type_assertion(content_type: str, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.CONTENT_TYPE,
        description=f"Content-Type da resposta deve conter '{content_type}'.",
        expected_value=content_type,
        origin=origin,
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


def _generate(request: dict, assertions: tuple[AssertionDefinition, ...]):
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)


# --- application/json --------------------------------------------------------


def test_application_json_generates_an_exact_media_type_assertion():
    generated = _generate(_GET_USERS, (_content_type_assertion("application/json"),))

    assert 'content_type = response.headers.get("content-type", "")' in generated.content
    assert 'assert content_type.split(";")[0].strip().lower() == "application/json"' in (
        generated.content
    )
    ast.parse(generated.content)


def test_application_json_with_charset_is_reduced_to_the_media_type():
    # "application/json; charset=utf-8" -> "application/json" — nunca a
    # string completa com parâmetro no valor esperado.
    generated = _generate(
        _GET_USERS, (_content_type_assertion("application/json; charset=utf-8"),)
    )

    assert 'assert content_type.split(";")[0].strip().lower() == "application/json"' in (
        generated.content
    )
    assert "charset" not in generated.content
    ast.parse(generated.content)


# --- charset não causa falso negativo (comparação em runtime) ---------------


def test_charset_in_the_actual_response_never_causes_a_false_negative():
    # A asserção gerada separa media type de parâmetros do lado da resposta
    # também (content_type.split(";")[0]) — simulado aqui executando a
    # mesma expressão que o código gerado usa.
    expected = "application/json"
    actual_header_value = "application/json; charset=utf-8"

    assert actual_header_value.split(";")[0].strip().lower() == expected


# --- diferença de casing no nome/valor do header -----------------------------


def test_content_type_header_name_lookup_is_lowercase():
    # response.headers do Playwright já normaliza o NOME do header para
    # minúsculas — o código gerado sempre consulta pela chave minúscula,
    # cobrindo "Content-Type", "CONTENT-TYPE" etc. em runtime.
    generated = _generate(_GET_USERS, (_content_type_assertion("application/json"),))

    assert 'response.headers.get("content-type"' in generated.content
    assert 'response.headers.get("Content-Type"' not in generated.content


def test_content_type_value_comparison_is_case_insensitive():
    generated = _generate(_GET_USERS, (_content_type_assertion("Application/JSON"),))

    # Valor esperado normalizado já na geração — nunca "Application/JSON"
    # literal na comparação.
    assert '== "application/json"' in generated.content
    ast.parse(generated.content)


# --- media type incompatível: falha, nunca reclassificado como aceitável ---


def test_incompatible_media_type_still_asserts_the_exact_expected_value():
    # A asserção sempre compara contra o valor exato da evidência — um
    # media type "incompatível" (a resposta real seria outra coisa) faz a
    # asserção FALHAR em runtime; não existe nenhuma lógica aqui que aceite
    # um tipo "parecido" ou de uma classe genérica.
    generated = _generate(_GET_USERS, (_content_type_assertion("text/plain"),))

    assert 'assert content_type.split(";")[0].strip().lower() == "text/plain"' in (
        generated.content
    )
    assert "application/json" not in generated.content
    ast.parse(generated.content)


# --- media type +json suportado ----------------------------------------------


def test_plus_json_media_type_is_recognized_as_json_compatible():
    generated = _generate(
        _GET_USERS, (_content_type_assertion("application/vnd.api+json"),)
    )

    assert 'assert content_type.split(";")[0].strip().lower() == "application/vnd.api+json"' in (
        generated.content
    )
    assert "[JSON]" in generated.content
    ast.parse(generated.content)


def test_plain_json_is_tagged_as_json_and_non_json_is_not():
    json_generated = _generate(_GET_USERS, (_content_type_assertion("application/json"),))
    text_generated = _generate(_GET_USERS, (_content_type_assertion("text/csv"),))

    assert "[JSON]" in json_generated.content
    assert "[não-JSON]" in text_generated.content


# --- resposta sem body (204): nunca exige Content-Type sem evidência -------


def test_204_response_without_content_type_evidence_generates_no_assertion():
    generated = _generate(_GET_USERS, (_status_assertion(204),))

    assert "content_type" not in generated.content
    assert "Content-Type:" not in generated.content
    ast.parse(generated.content)


def test_absence_of_content_type_evidence_never_generates_a_warning():
    # Diferente de status (Parte 16): não ter evidência de Content-Type é o
    # caso normal (ex.: 204), nunca um warning.
    generated = _generate(_GET_USERS, (_status_assertion(204),))

    assert generated.warnings == ()


# --- header ausente em runtime: falha limpa, nunca crash ---------------------


def test_missing_header_at_runtime_fails_cleanly_instead_of_crashing():
    # .get("content-type", "") nunca .get("content-type") cru — um header
    # ausente vira "" (nunca None), então a comparação abaixo sempre falha
    # de forma limpa (AssertionError), nunca um AttributeError tentando
    # chamar .split(...) num None.
    generated = _generate(_GET_USERS, (_content_type_assertion("application/json"),))

    assert 'response.headers.get("content-type", "")' in generated.content
    assert 'response.headers.get("content-type")\n' not in generated.content


# --- status e Content-Type juntos: dois asserts independentes --------------


def test_status_and_content_type_assertions_coexist():
    generated = _generate(
        _GET_USERS,
        (_status_assertion(200), _content_type_assertion("application/json; charset=utf-8")),
    )

    assert "assert response.status == 200" in generated.content
    assert 'assert content_type.split(";")[0].strip().lower() == "application/json"' in (
        generated.content
    )
    # Status antes de Content-Type — mesma ordem de inspeção de uma
    # resposta HTTP (status, depois headers).
    assert generated.content.index("response.status == 200") < generated.content.index(
        "content_type ="
    )
    ast.parse(generated.content)


# --- origem registrada ---------------------------------------------------


def test_content_type_origin_is_recorded_in_the_docstring():
    generated = _generate(
        _GET_USERS, (_content_type_assertion("application/json", origin="example"),)
    )

    assert "Content-Type: application/json [JSON] (origem: example)" in generated.content


# --- geração determinística ---------------------------------------------------


def test_content_type_assertion_generation_is_deterministic():
    assertions = (_content_type_assertion("application/json; charset=utf-8"),)

    first = _generate(_GET_USERS, assertions).content
    second = _generate(_GET_USERS, assertions).content

    assert first == second


# --- fluxo Postman inalterado -------------------------------------------------


def test_postman_flow_keeps_generating_the_same_content_type_assertion():
    strategy = TestStrategy(
        endpoint_source="GET /users",
        assertions=(_content_type_assertion("application/json"),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PostmanTestGenerator().generate(strategy)

    assert 'pm.response.headers.get("Content-Type")' in generated.script
    assert 'pm.expect(contentType).to.include("application/json");' in generated.script


# --- geração sintaticamente válida em todo cenário --------------------------


# --- P1.1 (complementação): registro de AssertionResult ---------------------


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None, body_text: str = ""):
        self.status = status
        self.headers = headers or {}
        self._body_text = body_text

    def text(self) -> str:
        return self._body_text


class _FakeApiContext:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def get(self, path, params=None, headers=None, data=None, multipart=None):
        return self._response


def _load_generated_test_function(content: str):
    # Mesmo texto exato que vira o arquivo gerado — nunca uma cópia
    # reescrita à mão que possa divergir (mesmo padrão de
    # test_playwright_false_positive_prevention.py).
    ast.parse(content)
    namespace: dict = {}
    exec(content, namespace)  # noqa: S102 - texto do próprio gerador, não input externo
    functions = [
        value for key, value in namespace.items() if key.startswith("test_") and callable(value)
    ]
    assert len(functions) == 1
    return functions[0]


def test_content_type_assertion_records_a_passed_result(tmp_path, monkeypatch):
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))

    analysis, normalized_request = _analyzed(_GET_USERS)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=(_content_type_assertion("application/json", origin="contract"),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)
    test_function = _load_generated_test_function(generated.content)

    test_function(
        _FakeApiContext(
            _FakeResponse(status=200, headers={"content-type": "application/json"}, body_text="{}")
        )
    )

    lines = results_path.read_text(encoding="utf-8").strip().splitlines()
    recorded = [json.loads(line) for line in lines]
    content_type_entry = next(e for e in recorded if e["name"] == "Content-Type")
    assert content_type_entry["expected"] == "application/json"
    assert content_type_entry["actual"] == "application/json"
    assert content_type_entry["status"] == "PASSED"
    assert content_type_entry["precision"] == "EXACT"
    assert "contract" in content_type_entry["reason"]


def test_content_type_assertion_records_a_failed_result_and_still_raises(tmp_path, monkeypatch):
    results_path = tmp_path / "assertion-results.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_ASSERTION_RESULTS_PATH", str(results_path))

    analysis, normalized_request = _analyzed(_GET_USERS)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=(_content_type_assertion("application/json", origin="contract"),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)
    test_function = _load_generated_test_function(generated.content)

    with pytest.raises(AssertionError):
        test_function(_FakeApiContext(_FakeResponse(status=200, headers={"content-type": "text/html"})))

    lines = results_path.read_text(encoding="utf-8").strip().splitlines()
    recorded = [json.loads(line) for line in lines]
    content_type_entry = next(e for e in recorded if e["name"] == "Content-Type")
    assert content_type_entry["expected"] == "application/json"
    assert content_type_entry["actual"] == "text/html"
    assert content_type_entry["status"] == "FAILED"


def test_content_type_assertion_result_shares_the_test_id_with_its_transaction():
    # Correlação test_id -> assertion (o outro lado, test_id -> transação,
    # já é responsabilidade da fixture autouse de conftest.py — Parte P1.2)
    # — aqui só confirma que o mesmo test_id literal (nome da função gerada)
    # é o que chega em cada _record_assertion_result do Content-Type.
    analysis, normalized_request = _analyzed(_GET_USERS)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=(_content_type_assertion("application/json"),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)

    assert "'test_get_users_success', \"Content-Type\"" in generated.content


def test_all_content_type_scenarios_produce_syntactically_valid_python():
    scenarios = (
        (_content_type_assertion("application/json"),),
        (_content_type_assertion("application/json; charset=utf-8"),),
        (_content_type_assertion("application/vnd.api+json"),),
        (_content_type_assertion("text/plain"),),
        (_status_assertion(204),),
        (_status_assertion(200), _content_type_assertion("application/json")),
    )
    for assertions in scenarios:
        generated = _generate(_GET_USERS, assertions)
        ast.parse(generated.content)
