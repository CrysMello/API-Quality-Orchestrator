"""Parte 22 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
validação funcional de VALORES de resposta, só quando há evidência
confiável — "const"/"enum" (valor explícito no contrato) e correlação
request->response via a extensão "x-source-request-field" (nunca por
coincidência de nome de campo). Nunca compara o body inteiro, nunca inventa
expectativa para campos dinâmicos (id/timestamp/token/...) sem evidência
explícita, e a mensagem de falha nunca expõe o valor esperado ou recebido
(só o nome do campo).

A primeira seção testa o comportamento em RUNTIME executando o texto exato
que _resolve_expected_values_assertion produz (o mesmo embutido no arquivo
gerado, nunca uma cópia que possa divergir). A segunda seção testa o
CONTEÚDO gerado, mesmo padrão já usado pelas Partes 16-21.
"""

import ast
import json

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    BodyMode,
    NormalizedBody,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import PlaywrightEndpointTestGenerator
from api_quality_agent.generators.playwright.playwright_endpoint_test_generator import (
    _BodyJsonResolution,
    _resolve_expected_values_assertion,
)
from api_quality_agent.parsers import PostmanCollectionParser

# --- Runtime: mesmo texto embutido no arquivo gerado, executado de verdade -


def _schema_assertion(schema: dict, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.SCHEMA,
        description="O corpo da resposta deve validar contra o schema esperado.",
        expected_value=schema,
        origin=origin,
    )


def _raw_request_body(raw: dict | None) -> NormalizedBody:
    if raw is None:
        return NormalizedBody(
            mode=BodyMode.NONE,
            content_type=None,
            has_content=False,
            text_content=None,
            fields=(),
            graphql_query=None,
            variable_references=(),
        )

    text = json.dumps(raw)
    return NormalizedBody(
        mode=BodyMode.RAW,
        content_type="application/json",
        has_content=True,
        text_content=text,
        fields=(),
        graphql_query=None,
        variable_references=(),
    )


def _build_validator(schema: dict, request_json: dict | None = None):
    # Mesmo texto exato que iria para o arquivo gerado (via
    # _resolve_expected_values_assertion), só envolvido numa função para
    # poder ser chamado com um `body`/`request_body` de teste.
    strategy = TestStrategy(
        endpoint_source="GET /x",
        assertions=(_schema_assertion(schema),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    body_resolution = _BodyJsonResolution(
        lines=("    body = ...\n",), docstring_note="", warning=None, extra_imports=frozenset()
    )
    resolution = _resolve_expected_values_assertion(
        strategy, body_resolution, _raw_request_body(request_json)
    )
    assert resolution.lines, "schema deveria gerar ao menos uma checagem de valor"
    source = (
        "def _get_nested_value(node, path):\n"
        "    for key in path:\n"
        "        if not isinstance(node, dict) or key not in node:\n"
        "            return None\n"
        "        node = node[key]\n"
        "    return node\n"
        "\n\n"
        "import pytest\n\n\n"
        "def _run(body, request_body=None):\n" + "".join(resolution.lines)
    )
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102 - texto do próprio gerador, não input externo
    return namespace["_run"]


def test_const_value_passes_when_matching():
    validator = _build_validator({"type": "object", "properties": {"status": {"const": "active"}}})
    validator({"status": "active"})  # nunca levanta


def test_const_value_fails_without_exposing_the_values():
    import pytest

    validator = _build_validator({"type": "object", "properties": {"status": {"const": "active"}}})

    with pytest.raises(pytest.fail.Exception) as excinfo:
        validator({"status": "banned"})

    message = str(excinfo.value)
    assert "status" in message
    assert "active" not in message
    assert "banned" not in message


def test_enum_value_passes_when_included():
    validator = _build_validator(
        {"type": "object", "properties": {"role": {"enum": ["admin", "user"]}}}
    )
    validator({"role": "admin"})
    validator({"role": "user"})


def test_enum_value_fails_without_exposing_the_values():
    import pytest

    validator = _build_validator(
        {"type": "object", "properties": {"role": {"enum": ["admin", "user"]}}}
    )

    with pytest.raises(pytest.fail.Exception) as excinfo:
        validator({"role": "superadmin"})

    message = str(excinfo.value)
    assert "role" in message
    assert "admin" not in message
    assert "superadmin" not in message


def test_correlation_passes_when_response_echoes_the_request_value():
    validator = _build_validator(
        {"type": "object", "properties": {"name": {"x-source-request-field": "name"}}},
        request_json={"name": "Maria"},
    )
    validator({"name": "Maria"}, request_body={"name": "Maria"})


def test_correlation_fails_without_exposing_the_values():
    import pytest

    validator = _build_validator(
        {"type": "object", "properties": {"name": {"x-source-request-field": "name"}}},
        request_json={"name": "Maria"},
    )

    with pytest.raises(pytest.fail.Exception) as excinfo:
        validator({"name": "Outro Nome"}, request_body={"name": "Maria"})

    message = str(excinfo.value)
    assert "name" in message
    assert "Maria" not in message
    assert "Outro Nome" not in message


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


_CREATE_USER_REQUEST = {
    "request": {
        "method": "POST",
        "url": "https://api.exemplo.com/users",
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": {"mode": "raw", "raw": json.dumps({"name": "Maria", "role": "admin"})},
    }
}


# --- valores sustentados são comparados -------------------------------------


def test_const_field_generates_an_equality_check():
    schema = {"type": "object", "properties": {"status": {"type": "string", "const": "active"}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_value = _get_nested_value(body, ('status',))" in generated.content
    assert 'if _value != "active":' in generated.content
    assert "Valor inesperado para o campo 'status' (ver contrato)." in generated.content
    ast.parse(generated.content)


def test_single_value_enum_is_treated_as_const():
    schema = {"type": "object", "properties": {"kind": {"type": "string", "enum": ["fixed"]}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert 'if _value != "fixed":' in generated.content


def test_multi_value_enum_generates_an_inclusion_check():
    schema = {
        "type": "object",
        "properties": {"role": {"type": "string", "enum": ["admin", "user"]}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "if _value not in (\"admin\", \"user\",):" in generated.content
    ast.parse(generated.content)


def test_correlation_generates_the_documented_example_assertion():
    # Exatamente o exemplo do plano: request_body["name"] = "Maria" e o
    # contrato marca response.name como originado desse campo do request.
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "x-source-request-field": "name"}},
    }
    generated = _generate(_CREATE_USER_REQUEST, _base_assertions(_schema_assertion(schema)))

    assert "_value = _get_nested_value(body, ('name',))" in generated.content
    assert 'if _value != request_body.get("name"):' in generated.content
    ast.parse(generated.content)


# --- correlação só com evidência real ---------------------------------------


def test_correlation_never_triggers_when_source_field_was_not_actually_sent():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "x-source-request-field": "nickname"}},
    }
    generated = _generate(_CREATE_USER_REQUEST, _base_assertions(_schema_assertion(schema)))

    # A chave ainda aparece no literal do JSON Schema embutido pela Parte
    # 21 (nunca removida do schema — só ignorada pelo jsonschema, que
    # descarta palavras-chave desconhecidas) — o que nunca acontece é a
    # CORRELAÇÃO em si, já que "nickname" nunca foi enviado no request.
    assert "request_body.get(" not in generated.content
    assert "Expected values:" not in generated.content


def test_correlation_never_triggers_without_a_json_request_body():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "x-source-request-field": "name"}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "request_body.get(" not in generated.content


def test_field_name_matching_alone_never_implies_correlation():
    # Regra 1: nunca assume que todo campo enviado será devolvido — mesmo
    # nome em request e response, sem o marcador explícito, nunca gera
    # nada.
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    generated = _generate(_CREATE_USER_REQUEST, _base_assertions(_schema_assertion(schema)))

    assert "request_body.get(" not in generated.content
    assert "Expected values:" not in generated.content


# --- campos dinâmicos nunca recebem expectativa inventada -------------------


def test_id_field_without_explicit_evidence_never_gets_a_value_expectation():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_get_nested_value(body, ('id',))" not in generated.content
    assert "Expected values:" not in generated.content


def test_id_field_with_explicit_const_is_still_honored():
    # "Salvo quando explicitamente documentado" (regra 2) — const é
    # evidência explícita, mesmo para um campo chamado "id".
    schema = {"type": "object", "properties": {"id": {"type": "string", "const": "fixed-id"}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_get_nested_value(body, ('id',))" in generated.content
    assert 'if _value != "fixed-id":' in generated.content


# --- nunca compara o response inteiro ---------------------------------------


def test_never_generates_a_whole_body_comparison():
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "status": {"type": "string", "const": "active"},
        },
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "body ==" not in generated.content
    assert "== body" not in generated.content


# --- valores sensíveis nunca aparecem na mensagem de falha ------------------


def test_secret_looking_const_value_never_appears_in_generated_message_text():
    schema = {
        "type": "object",
        "properties": {"apiKey": {"type": "string", "const": "sk_live_super_secret_123"}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    # O valor aparece só na estrutura de dados comparada (nunca escondido
    # do código gerado em si — não é uma variável de ambiente), mas NUNCA
    # dentro do texto de uma mensagem de falha (pytest.fail(...)).
    for line in generated.content.splitlines():
        if "pytest.fail(" in line and "Expected values" not in line:
            assert "sk_live_super_secret_123" not in line


def test_never_uses_a_bare_assert_for_value_comparisons():
    # Regra 6: um `assert _value == X` cru exibiria os dois valores reais
    # via o assertion rewriting do pytest, mesmo com mensagem customizada.
    schema = {"type": "object", "properties": {"status": {"type": "string", "const": "active"}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "assert _value" not in generated.content


# --- ausência de evidência: nunca inventa -----------------------------------


def test_no_schema_evidence_never_generates_expected_value_assertions():
    generated = _generate(_GET_USERS, _base_assertions())

    assert "_get_nested_value" not in generated.content
    assert "Expected values:" not in generated.content


def test_no_body_evidence_never_generates_expected_value_assertions_even_with_schema():
    schema = {"type": "object", "properties": {"status": {"const": "active"}}}
    generated = _generate(_GET_USERS, (_status_assertion(), _schema_assertion(schema)))

    assert "_get_nested_value" not in generated.content


# --- origem registrada ---------------------------------------------------


def test_expected_values_origin_is_recorded_in_the_docstring():
    schema = {"type": "object", "properties": {"status": {"const": "active"}}}
    generated = _generate(
        _GET_USERS, _base_assertions(_schema_assertion(schema, origin="example"))
    )

    assert "Expected values: 1 campo(s) validados (origem: example)" in generated.content


# --- coexiste com as partes anteriores --------------------------------------


def test_coexists_with_required_and_type_checks_for_the_same_endpoint():
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "status": {"type": "string", "const": "active"},
        },
        "required": ["id"],
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "_assert_required_field_present(body, ('id',))" in generated.content
    assert "_assert_field_type(body, ('id',), 'string', False)" in generated.content
    assert 'if _value != "active":' in generated.content
    ast.parse(generated.content)


# --- geração sintaticamente válida em todo cenário --------------------------


def test_all_expected_value_scenarios_produce_syntactically_valid_python():
    scenarios = [
        (_GET_USERS, _base_assertions()),
        (_GET_USERS, _base_assertions(_schema_assertion({"type": "object", "properties": {}}))),
        (
            _GET_USERS,
            _base_assertions(
                _schema_assertion(
                    {
                        "type": "object",
                        "properties": {
                            "status": {"const": "active"},
                            "role": {"enum": ["admin", "user"]},
                        },
                    }
                )
            ),
        ),
        (
            _CREATE_USER_REQUEST,
            _base_assertions(
                _schema_assertion(
                    {
                        "type": "object",
                        "properties": {"name": {"x-source-request-field": "name"}},
                    }
                )
            ),
        ),
    ]
    for request, assertions in scenarios:
        generated = _generate(request, assertions)
        ast.parse(generated.content)
