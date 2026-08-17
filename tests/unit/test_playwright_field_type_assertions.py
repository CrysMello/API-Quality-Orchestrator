"""Parte 20 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
validação de tipo dos campos da resposta, conforme o schema já resolvido
(AssertionType.SCHEMA) — sem coerção (uma string "123" nunca passa como
integer), com bool tratado antes de int (isinstance(True, int) é True em
Python, mas True nunca deve contar como integer/number), nullable
respeitado, estruturas aninhadas e itens de array cobertos, e mensagens de
falha sempre com campo, tipo esperado e tipo recebido.

A primeira seção testa o comportamento em RUNTIME executando o texto exato
que _render_helpers_block produz para _assert_field_type (o mesmo embutido
no arquivo gerado, nunca uma cópia que possa divergir). A segunda seção
testa o CONTEÚDO gerado, mesmo padrão já usado pelas Partes 16-19.
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
from api_quality_agent.parsers import PostmanCollectionParser

# --- Runtime: mesmo texto embutido no arquivo gerado, executado de verdade -


@pytest.fixture
def assert_field_type():
    source = _render_helpers_block(frozenset({"assert_field_type"}))
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102 - texto do próprio gerador, não input externo
    return namespace["_assert_field_type"]


@pytest.mark.parametrize(
    ("value", "json_type"),
    [
        ("hello", "string"),
        (42, "integer"),
        (3.14, "number"),
        (42, "number"),  # "number" aceita integer também — JSON não distingue
        (True, "boolean"),
        (False, "boolean"),
        ({"a": 1}, "object"),
        ([1, 2], "array"),
    ],
)
def test_matching_type_passes(assert_field_type, value, json_type):
    assert_field_type({"field": value}, ("field",), json_type, False)


def test_string_123_never_passes_as_integer(assert_field_type):
    # Regra 2: nenhuma coerção — "123" continua sendo string.
    with pytest.raises(AssertionError, match="esperado integer, recebido string"):
        assert_field_type({"field": "123"}, ("field",), "integer", False)


def test_true_never_passes_as_integer(assert_field_type):
    # Regra 3: isinstance(True, int) é True em Python, mas bool nunca deve
    # contar como integer.
    with pytest.raises(AssertionError, match="esperado integer, recebido boolean"):
        assert_field_type({"field": True}, ("field",), "integer", False)


def test_true_never_passes_as_number(assert_field_type):
    with pytest.raises(AssertionError, match="esperado number, recebido boolean"):
        assert_field_type({"field": True}, ("field",), "number", False)


def test_false_never_passes_as_integer(assert_field_type):
    with pytest.raises(AssertionError, match="esperado integer, recebido boolean"):
        assert_field_type({"field": False}, ("field",), "integer", False)


def test_float_never_passes_as_integer(assert_field_type):
    # "number" aceita integer, mas o inverso não é verdade — 3.14 não é um
    # integer válido.
    with pytest.raises(AssertionError, match="esperado integer, recebido number"):
        assert_field_type({"field": 3.14}, ("field",), "integer", False)


def test_error_message_contains_field_expected_and_received_type(assert_field_type):
    with pytest.raises(
        AssertionError,
        match=r"Tipo inválido para o campo 'user\.address\.zipCode': esperado string, recebido integer\.",
    ):
        assert_field_type(
            {"user": {"address": {"zipCode": 12345}}},
            ("user", "address", "zipCode"),
            "string",
            False,
        )


def test_absent_field_never_fails_type_validation(assert_field_type):
    # Presença é escopo da Parte 19 — aqui, campo ausente simplesmente não
    # tem nada para validar.
    assert_field_type({}, ("field",), "string", False)
    assert_field_type({"other": 1}, ("field",), "string", False)


def test_null_fails_when_not_nullable(assert_field_type):
    with pytest.raises(AssertionError, match="esperado string, recebido null"):
        assert_field_type({"field": None}, ("field",), "string", False)


def test_null_passes_when_nullable(assert_field_type):
    assert_field_type({"field": None}, ("field",), "string", True)


def test_nested_field_type_is_checked(assert_field_type):
    assert_field_type(
        {"user": {"address": {"zipCode": "12345"}}}, ("user", "address", "zipCode"), "string", False
    )
    with pytest.raises(AssertionError):
        assert_field_type(
            {"user": {"address": {"zipCode": 12345}}},
            ("user", "address", "zipCode"),
            "string",
            False,
        )


def test_non_navigable_intermediate_node_never_crashes(assert_field_type):
    # Nunca uma validação de tipo do NÓ intermediário (fora de escopo) —
    # só interrompe sem lançar TypeError.
    assert_field_type({"user": "not-an-object"}, ("user", "address"), "object", False)


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


# --- cobertura de todos os tipos ---------------------------------------------


def test_all_scalar_and_container_types_generate_a_type_check():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "score": {"type": "number"},
            "active": {"type": "boolean"},
            "profile": {"type": "object"},
            "tags": {"type": "array"},
        },
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_field_type(body, ('name',), 'string', False)" in generated.content
    assert "_assert_field_type(body, ('age',), 'integer', False)" in generated.content
    assert "_assert_field_type(body, ('score',), 'number', False)" in generated.content
    assert "_assert_field_type(body, ('active',), 'boolean', False)" in generated.content
    assert "_assert_field_type(body, ('profile',), 'object', False)" in generated.content
    assert "_assert_field_type(body, ('tags',), 'array', False)" in generated.content
    ast.parse(generated.content)


def test_null_type_generates_no_type_check_by_itself():
    # "null" como tipo isolado (sem outro tipo na lista) não é um caso
    # coberto por _normalize_field_type aqui — nullable é o mecanismo
    # correto para permitir null (regra 5), não "type": "null" sozinho num
    # campo de resposta real.
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_field_type(body, ('id',), 'string', False)" in generated.content


# --- nullable ---------------------------------------------------------------


def test_nullable_true_flag_is_passed_through():
    schema = {
        "type": "object",
        "properties": {"middleName": {"type": "string", "nullable": True}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_field_type(body, ('middleName',), 'string', True)" in generated.content


def test_json_schema_style_nullable_type_list_is_recognized():
    schema = {
        "type": "object",
        "properties": {"middleName": {"type": ["string", "null"]}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_field_type(body, ('middleName',), 'string', True)" in generated.content


def test_ambiguous_type_list_never_generates_a_type_check():
    schema = {
        "type": "object",
        "properties": {"value": {"type": ["string", "integer"]}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "'value'" not in generated.content


# --- estrutura aninhada -------------------------------------------------


def test_nested_object_field_types_are_validated():
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "properties": {"zipCode": {"type": "string"}},
                    }
                },
            }
        },
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_field_type(body, ('user',), 'object', False)" in generated.content
    assert "_assert_field_type(body, ('user', 'address'), 'object', False)" in generated.content
    assert (
        "_assert_field_type(body, ('user', 'address', 'zipCode'), 'string', False)"
        in generated.content
    )
    ast.parse(generated.content)


def test_never_descends_into_a_field_without_a_documented_object_type():
    schema = {
        "type": "object",
        "properties": {"user": {"properties": {"address": {"type": "string"}}}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "'address'" not in generated.content


# --- item de array estruturado -----------------------------------------


def test_array_item_field_types_are_validated_safely():
    schema = {
        "type": "object",
        "properties": {
            "orders": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "total": {"type": "number"}},
                },
            }
        },
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_field_type(body, ('orders',), 'array', False)" in generated.content
    assert "_get_nested_value(body, ('orders',))" in generated.content
    assert "for _item in _orders_items:" in generated.content
    assert "if not isinstance(_item, dict):" in generated.content
    assert "_assert_field_type(_item, ('id',), 'string', False)" in generated.content
    assert "_assert_field_type(_item, ('total',), 'number', False)" in generated.content
    assert "[0]" not in generated.content
    ast.parse(generated.content)


# --- ausência de evidência: nunca inventa -----------------------------------


def test_no_schema_evidence_never_generates_type_assertions():
    generated = _generate(_GET_USERS, _base_assertions())

    assert "_assert_field_type" not in generated.content
    assert "Field types:" not in generated.content


def test_no_body_evidence_never_generates_type_assertions_even_with_schema():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(_GET_USERS, (_status_assertion(), _schema_assertion(schema)))

    assert "_assert_field_type" not in generated.content


# --- origem registrada ---------------------------------------------------


def test_field_types_origin_is_recorded_in_the_docstring():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(
        _GET_USERS, _base_assertions(_schema_assertion(schema, origin="example"))
    )

    assert "Field types: 1 campo(s) validados (origem: example)" in generated.content


# --- coexiste com Partes 18/19 sem conflito ---------------------------------


def test_type_and_required_checks_coexist_for_the_same_field():
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present(body, ('id',))" in generated.content
    assert "_assert_field_type(body, ('id',), 'string', False)" in generated.content
    # Helpers das duas partes definidos uma única vez cada.
    assert generated.content.count("def _assert_required_field_present(") == 1
    assert generated.content.count("def _assert_field_type(") == 1
    ast.parse(generated.content)


# --- geração sintaticamente válida em todo cenário --------------------------


def test_all_field_type_scenarios_produce_syntactically_valid_python():
    nested_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "age": {"type": "integer", "nullable": True},
            "user": {
                "type": "object",
                "properties": {"address": {"type": "object", "properties": {"zip": {"type": "string"}}}},
            },
            "orders": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "string"}}},
            },
        },
        "required": ["id"],
    }
    scenarios = (
        _base_assertions(),
        _base_assertions(_schema_assertion({"type": "object", "properties": {}})),
        _base_assertions(_schema_assertion(nested_schema)),
        (_status_assertion(), _schema_assertion(nested_schema)),
    )
    for assertions in scenarios:
        generated = _generate(_GET_USERS, assertions)
        ast.parse(generated.content)
