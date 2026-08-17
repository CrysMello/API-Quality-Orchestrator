"""Parte 19 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
validação de presença de campos obrigatórios, só quando documentados em
AssertionType.SCHEMA (nunca inferidos de um exemplo) — suporta campos
aninhados, diferencia ausência de presença-com-null, nunca acessa item[0]
de um array sem garantir existência, e sempre reporta o caminho completo
(ex.: "user.address.zipCode") na falha.

A primeira seção testa o comportamento em RUNTIME executando o texto exato
que _render_helpers_block produz para estes dois helpers (o mesmo embutido
no arquivo gerado, nunca uma cópia que possa divergir). A segunda seção
testa o CONTEÚDO gerado, mesmo padrão já usado pelas Partes 16-18.
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
from api_quality_agent.generators.playwright.playwright_endpoint_test_generator import (
    _render_helpers_block,
)
from api_quality_agent.generators.postman_test_generator import PostmanTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser

# --- Runtime: mesmo texto embutido no arquivo gerado, executado de verdade -


@pytest.fixture
def helpers():
    source = _render_helpers_block(
        frozenset({"assert_required_field_present", "get_nested_value"})
    )
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102 - texto do próprio gerador, não input externo
    return namespace


def test_required_field_present_passes(helpers):
    helpers["_assert_required_field_present"]({"id": "1"}, ("id",))


def test_required_field_absent_fails_with_the_full_path(helpers):
    with pytest.raises(AssertionError, match=r"Campo obrigatório ausente: user\.address\.zipCode"):
        helpers["_assert_required_field_present"](
            {"user": {"address": {}}}, ("user", "address", "zipCode")
        )


def test_nested_field_present_passes(helpers):
    helpers["_assert_required_field_present"](
        {"user": {"address": {"zipCode": "12345"}}}, ("user", "address", "zipCode")
    )


def test_field_present_with_null_never_fails(helpers):
    # "Diferenciar campo ausente de campo presente com null" — presente
    # (mesmo que null) nunca é tratado como ausente.
    helpers["_assert_required_field_present"]({"address": None}, ("address",))


def test_nullable_parent_skips_children_instead_of_crashing(helpers):
    # "Respeitar nullable quando documentado": um pai null nunca falha só
    # por não ter como navegar até o filho, e nunca lança TypeError.
    helpers["_assert_required_field_present"](
        {"user": None}, ("user", "address", "zipCode")
    )


def test_non_dict_intermediate_node_never_crashes(helpers):
    # Nunca uma validação de tipo (fora de escopo) — um nó inesperadamente
    # não-navegável simplesmente interrompe, nunca lança exceção.
    helpers["_assert_required_field_present"]({"user": "not-an-object"}, ("user", "address"))


def test_empty_array_via_get_nested_value_and_iteration_never_fails(helpers):
    value = helpers["_get_nested_value"]({"orders": []}, ("orders",))
    assert value == []
    # Iterar uma lista vazia nunca executa o corpo do loop — nenhuma falha.
    for _item in value:
        raise AssertionError("não deveria iterar")


def test_get_nested_value_never_indexes_a_missing_key(helpers):
    assert helpers["_get_nested_value"]({}, ("orders",)) is None
    assert helpers["_get_nested_value"]({"orders": None}, ("orders", "0")) is None


# --- Conteúdo gerado ----------------------------------------------------


_GET_USERS = {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}


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


def _base_assertions(*extra: AssertionDefinition) -> tuple[AssertionDefinition, ...]:
    return (_status_assertion(), _valid_json_body_assertion(), *extra)


# --- campo obrigatório: presença gerada corretamente ------------------------


def test_required_field_generates_a_presence_assertion():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present(body, ('id',))" in generated.content
    assert "def _assert_required_field_present(node, path):" in generated.content
    ast.parse(generated.content)


# --- campo opcional ausente: nunca gera asserção ---------------------------


def test_optional_field_never_generates_a_presence_assertion():
    # Um campo opcional nunca vira _assert_required_field_present — a
    # validação de TIPO de campos opcionais (quando presentes) é escopo da
    # Parte 20, não desta; aqui só a presença/obrigatoriedade importa.
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "nickname": {"type": "string"}},
        "required": ["id"],
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present(body, ('nickname',))" not in generated.content
    assert "_assert_required_field_present(body, ('id',))" in generated.content
    ast.parse(generated.content)


def test_schema_without_any_required_field_generates_nothing():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present" not in generated.content
    assert "def _assert_required_field_present" not in generated.content
    assert "Required fields:" not in generated.content


# --- campo aninhado -----------------------------------------------------


def test_nested_required_field_uses_the_full_path():
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "properties": {"zipCode": {"type": "string"}},
                        "required": ["zipCode"],
                    }
                },
                "required": ["address"],
            }
        },
        "required": ["user"],
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present(body, ('user',))" in generated.content
    assert "_assert_required_field_present(body, ('user', 'address'))" in generated.content
    assert (
        "_assert_required_field_present(body, ('user', 'address', 'zipCode'))"
        in generated.content
    )
    ast.parse(generated.content)


# --- campo nullable: gerado normalmente (a checagem em runtime já cobre) --


def test_nullable_field_still_generates_a_presence_assertion():
    schema = {
        "type": "object",
        "properties": {"address": {"type": ["object", "null"]}},
        "required": ["address"],
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present(body, ('address',))" in generated.content
    # "type" ambíguo (lista) — nunca desce para gerar requireds do que teria
    # dentro de "address", mesmo que ele também declarasse "required".
    ast.parse(generated.content)


# --- lista vazia / item de array estruturado --------------------------------


def test_array_field_with_structured_items_generates_a_safe_iteration():
    schema = {
        "type": "object",
        "properties": {
            "orders": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            }
        },
        "required": ["orders"],
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present(body, ('orders',))" in generated.content
    assert "_get_nested_value(body, ('orders',))" in generated.content
    assert "for _item in _orders_items:" in generated.content
    assert "if not isinstance(_item, dict):" in generated.content
    assert "continue" in generated.content
    assert 'assert _field in _item, "Campo obrigatório ausente: orders[]." + _field' in (
        generated.content
    )
    # Nunca indexado por posição.
    assert "[0]" not in generated.content
    ast.parse(generated.content)


def test_array_field_without_item_schema_generates_only_presence():
    schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
        "required": ["tags"],
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present(body, ('tags',))" in generated.content
    # O helper _get_nested_value é definido (sempre junto do outro helper),
    # mas nunca CHAMADO — nenhuma iteração de item foi gerada para "tags",
    # já que seu "items" não é um schema de objeto com "required" próprio.
    assert "_get_nested_value(body," not in generated.content
    assert "for _item in" not in generated.content


# --- ausência de evidência estrutural: nunca inventa -----------------------


def test_no_schema_evidence_never_generates_required_field_assertions():
    generated = _generate(_GET_USERS, _base_assertions())

    assert "_assert_required_field_present" not in generated.content
    assert "Required fields:" not in generated.content


def test_no_body_evidence_never_generates_required_field_assertions_even_with_schema():
    # Schema presente, mas nenhuma evidência de que a resposta é JSON — sem
    # `body`, não há como (nem por quê) validar campos dentro dele.
    schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    generated = _generate(_GET_USERS, (_status_assertion(), _schema_assertion(schema)))

    assert "_assert_required_field_present" not in generated.content
    assert "json.loads" not in generated.content
    assert "body =" not in generated.content


# --- origem registrada ---------------------------------------------------


def test_required_fields_origin_is_recorded_in_the_docstring():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    generated = _generate(
        _GET_USERS, _base_assertions(_schema_assertion(schema, origin="example"))
    )

    assert "Required fields: 1 campo(s) obrigatório(s) validados (origem: example)" in (
        generated.content
    )


# --- fluxo Postman inalterado -------------------------------------------------


def test_postman_flow_keeps_generating_the_same_required_field_assertion():
    strategy = TestStrategy(
        endpoint_source="GET /users",
        assertions=(
            AssertionDefinition(
                assertion_type=AssertionType.REQUIRED_FIELD_PRESENT,
                description="O campo obrigatório 'id' deve estar presente na resposta.",
                expected_value={"field": "id", "must_have_value": False},
                origin="contract",
            ),
        ),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PostmanTestGenerator().generate(strategy)

    assert 'to.have.property("id")' in generated.script


# --- geração sintaticamente válida em todo cenário --------------------------


def test_all_required_field_scenarios_produce_syntactically_valid_python():
    nested_schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {"address": {"type": "object", "required": ["zipCode"]}},
                "required": ["address"],
            },
            "orders": {
                "type": "array",
                "items": {"type": "object", "required": ["id"]},
            },
        },
        "required": ["user", "orders"],
    }
    scenarios = (
        _base_assertions(),
        _base_assertions(_schema_assertion({"type": "object", "required": []})),
        _base_assertions(_schema_assertion(nested_schema)),
        (_status_assertion(), _schema_assertion(nested_schema)),
    )
    for assertions in scenarios:
        generated = _generate(_GET_USERS, assertions)
        ast.parse(generated.content)
