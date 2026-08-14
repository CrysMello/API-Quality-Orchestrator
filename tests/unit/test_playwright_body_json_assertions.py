"""Parte 18 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
validação do body JSON — parse só quando há evidência (Content-Type
compatível ou AssertionType.VALID_JSON_BODY), corpo vazio tratado como
categoria própria (nunca só "JSON inválido"), estrutura do nível superior
(dict/list/escalar/null) validada apenas quando documentada (SCHEMA), e o
body parseado (`body`) sempre disponível para reaproveitamento nas partes
seguintes — nunca `assert response.json() is not None`.
"""

import ast
import json

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    BODY_STRUCTURE_NOT_DETERMINED,
    PlaywrightEndpointTestGenerator,
)
from api_quality_agent.generators.postman_test_generator import PostmanTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser

_GET_USERS = {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}


def _status_assertion(status_code: int) -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
        origin="contract",
    )


def _content_type_assertion(content_type: str = "application/json") -> AssertionDefinition:
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


def _schema_assertion(schema: dict, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.SCHEMA,
        description="O corpo da resposta deve validar contra o schema esperado.",
        expected_value=schema,
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


# --- objeto JSON --------------------------------------------------------


def test_json_object_generates_a_dict_assertion():
    generated = _generate(
        _GET_USERS,
        (_status_assertion(200), _valid_json_body_assertion(), _schema_assertion({"type": "object"})),
    )

    assert "body = json.loads(body_text)" in generated.content
    assert "assert isinstance(body, dict)" in generated.content
    assert "isinstance(body, list)" not in generated.content
    assert "import json" in generated.content
    assert "import pytest" in generated.content
    assert generated.warnings == ()
    ast.parse(generated.content)


# --- array JSON -----------------------------------------------------------


def test_json_array_generates_a_list_assertion():
    generated = _generate(
        _GET_USERS,
        (_valid_json_body_assertion(), _schema_assertion({"type": "array"})),
    )

    assert "assert isinstance(body, list)" in generated.content
    assert "isinstance(body, dict)" not in generated.content
    ast.parse(generated.content)


# --- valor escalar e null (diferenciação exigida pela regra 3) -------------


def test_json_scalar_string_generates_a_str_assertion():
    generated = _generate(
        _GET_USERS,
        (_valid_json_body_assertion(), _schema_assertion({"type": "string"})),
    )

    assert "assert isinstance(body, str)" in generated.content


def test_json_null_generates_an_is_none_assertion():
    generated = _generate(
        _GET_USERS,
        (_valid_json_body_assertion(), _schema_assertion({"type": "null"})),
    )

    assert "assert body is None" in generated.content


def test_json_boolean_is_never_confused_with_integer():
    # bool é subclasse de int em Python — a asserção de "integer" precisa
    # excluir bool explicitamente, senão um body `true` passaria como
    # inteiro por engano.
    generated = _generate(
        _GET_USERS,
        (_valid_json_body_assertion(), _schema_assertion({"type": "integer"})),
    )

    assert "isinstance(body, int) and not isinstance(body, bool)" in generated.content


# --- JSON inválido: mensagem clara e identificável -------------------------


def test_invalid_json_is_handled_with_a_clear_and_identifiable_message():
    generated = _generate(_GET_USERS, (_valid_json_body_assertion(),))

    assert "try:" in generated.content
    assert "body = json.loads(body_text)" in generated.content
    assert "except json.JSONDecodeError as error:" in generated.content
    assert (
        'pytest.fail(f"Corpo da resposta não é um JSON válido: {error}")' in generated.content
    )
    ast.parse(generated.content)


# --- body vazio: categoria própria, nunca só "JSON inválido" ---------------


def test_empty_body_is_a_distinct_category_from_invalid_json():
    generated = _generate(_GET_USERS, (_valid_json_body_assertion(),))

    assert "if not body_text.strip():" in generated.content
    assert (
        'pytest.fail("Corpo da resposta vazio; esperado um JSON válido.")' in generated.content
    )
    # A checagem de vazio vem antes da tentativa de parse.
    assert generated.content.index("body_text.strip()") < generated.content.index(
        "json.loads(body_text)"
    )
    ast.parse(generated.content)


# --- JSON de erro em cenário positivo: só estrutura, nunca conteúdo -------


def test_error_shaped_json_in_a_positive_scenario_is_still_only_structurally_checked():
    # Um body de erro (`{"error": "not found"}`) ainda é um dict — a
    # asserção gerada nunca tenta detectar "isto parece um erro", só
    # valida a estrutura documentada (regra 5: nenhum JSON válido é tratado
    # como resposta funcionalmente correta só por ser bem formado).
    generated = _generate(
        _GET_USERS,
        (
            _status_assertion(200),
            _valid_json_body_assertion(),
            _schema_assertion({"type": "object"}),
        ),
    )

    assert "assert isinstance(body, dict)" in generated.content
    assert "Scenario: success" in generated.content
    # Nenhuma lógica de negócio inspecionando o conteúdo do body (ex.: um
    # campo "error"/"status" dentro do JSON) — só a estrutura documentada.
    assert 'body.get("error")' not in generated.content
    assert '"error" in body' not in generated.content
    assert "body[" not in generated.content
    ast.parse(generated.content)


# --- estrutura superior incompatível/não reconhecida -----------------------


def test_unrecognized_top_level_type_generates_no_assertion_and_warns():
    # "type" documentado mas não mapeável com segurança (aqui, uma lista de
    # tipos do JSON Schema) — nunca inventa uma asserção; body ainda é
    # parseado (prova que é JSON bem formado), mas sem isinstance.
    generated = _generate(
        _GET_USERS,
        (
            _status_assertion(200),
            _valid_json_body_assertion(),
            _schema_assertion({"type": ["object", "null"]}),
        ),
    )

    assert "body = json.loads(body_text)" in generated.content
    assert "isinstance(body," not in generated.content
    assert "assert body is None" not in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == BODY_STRUCTURE_NOT_DETERMINED


# --- ausência de evidência estrutural (VALID_JSON_BODY sem SCHEMA) --------


def test_absence_of_schema_evidence_still_parses_but_skips_structure_and_warns():
    generated = _generate(_GET_USERS, (_status_assertion(200), _valid_json_body_assertion()))

    assert "body = json.loads(body_text)" in generated.content
    assert "isinstance(body," not in generated.content
    assert len(generated.warnings) == 1
    warning = generated.warnings[0]
    assert warning.code == BODY_STRUCTURE_NOT_DETERMINED
    assert warning.endpoint == "GET /users"
    ast.parse(generated.content)


# --- ausência de evidência de JSON: nenhum parse, nenhum warning ----------


def test_no_json_evidence_at_all_never_parses_and_never_warns():
    generated = _generate(_GET_USERS, (_status_assertion(200),))

    assert "response.text()" not in generated.content
    assert "json.loads" not in generated.content
    assert "import json" not in generated.content
    assert generated.warnings == ()


def test_content_type_alone_is_enough_evidence_to_parse():
    # Regra 1: Content-Type compatível OU evidência explícita — aqui só o
    # primeiro está presente (sem AssertionType.VALID_JSON_BODY) e ainda
    # assim o parse acontece.
    generated = _generate(_GET_USERS, (_content_type_assertion("application/json"),))

    assert "body = json.loads(body_text)" in generated.content


def test_non_json_content_type_alone_never_triggers_a_parse():
    generated = _generate(_GET_USERS, (_content_type_assertion("text/csv"),))

    assert "json.loads" not in generated.content


# --- corpo reaproveitável, sem duplicar chamadas ---------------------------


def test_body_variable_is_assigned_once_and_reusable():
    generated = _generate(
        _GET_USERS,
        (_valid_json_body_assertion(), _schema_assertion({"type": "object"})),
    )

    assert generated.content.count("json.loads(body_text)") == 1
    assert generated.content.count("response.text()") == 1
    assert generated.content.count("body = json.loads") == 1


# --- geração determinística ---------------------------------------------------


def test_body_json_generation_is_deterministic():
    assertions = (_valid_json_body_assertion(), _schema_assertion({"type": "object"}))

    first = _generate(_GET_USERS, assertions).content
    second = _generate(_GET_USERS, assertions).content

    assert first == second


# --- origem registrada ---------------------------------------------------


def test_structure_origin_is_recorded_in_the_docstring():
    generated = _generate(
        _GET_USERS,
        (_valid_json_body_assertion(), _schema_assertion({"type": "object"}, origin="example")),
    )

    assert "[estrutura: object, origem: example]" in generated.content


# --- fluxo Postman inalterado -------------------------------------------------


def test_postman_flow_keeps_generating_the_same_valid_json_body_assertion():
    strategy = TestStrategy(
        endpoint_source="GET /users",
        assertions=(_valid_json_body_assertion(),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PostmanTestGenerator().generate(strategy)

    assert (
        'pm.expect(body, "o corpo da resposta não é um JSON válido").to.not.be.undefined;'
        in generated.script
    )


def test_postman_flow_keeps_generating_the_same_schema_assertion():
    strategy = TestStrategy(
        endpoint_source="GET /users",
        assertions=(_schema_assertion({"type": "object"}),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PostmanTestGenerator().generate(strategy)

    assert "pm.response.to.have.jsonSchema(" in generated.script


# --- geração sintaticamente válida em todo cenário --------------------------


def test_all_body_json_scenarios_produce_syntactically_valid_python():
    scenarios = (
        (_valid_json_body_assertion(), _schema_assertion({"type": "object"})),
        (_valid_json_body_assertion(), _schema_assertion({"type": "array"})),
        (_valid_json_body_assertion(), _schema_assertion({"type": "string"})),
        (_valid_json_body_assertion(), _schema_assertion({"type": "null"})),
        (_valid_json_body_assertion(),),
        (_status_assertion(200),),
        (_content_type_assertion("application/json"),),
        (_valid_json_body_assertion(), _schema_assertion({"type": ["object", "null"]})),
    )
    for assertions in scenarios:
        generated = _generate(_GET_USERS, assertions)
        ast.parse(generated.content)
