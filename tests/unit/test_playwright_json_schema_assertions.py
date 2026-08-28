"""Parte 21 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
validação estrutural completa por JSON Schema (lib `jsonschema` de verdade,
nunca uma reimplementação própria) — adicional às Partes 16-20 (status,
Content-Type, body, campos obrigatórios, tipos), nunca substitutiva.
Referência ($ref) local ao próprio documento é suportada (resolução
embutida do jsonschema, nunca sai do schema); qualquer outra referência
nunca é buscada automaticamente — vira warning, e a validação inteira desta
parte é pulada para aquele endpoint (nunca uma validação parcial disfarçada
de completa).

A primeira seção testa o comportamento em RUNTIME executando o texto exato
que _resolve_json_schema_assertion produz (o mesmo embutido no arquivo
gerado, nunca uma cópia que possa divergir), usando a lib jsonschema de
verdade. A segunda seção testa o CONTEÚDO gerado, mesmo padrão já usado
pelas Partes 16-20.
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
    JSON_SCHEMA_REF_NOT_SUPPORTED,
    PlaywrightEndpointTestGenerator,
)
from api_quality_agent.generators.playwright.playwright_endpoint_test_generator import (
    _BodyJsonResolution,
    _RECORD_ASSERTION_RESULT_SOURCE,
    _resolve_json_schema_assertion,
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


def _resolve(schema: dict, test_id: str = "test_x"):
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
    return _resolve_json_schema_assertion(strategy, body_resolution, test_id)


def _build_validator(schema: dict):
    # Mesmo texto exato que iria para o arquivo gerado (via
    # _resolve_json_schema_assertion), só envolvido numa função para poder
    # ser chamado com um `body` de teste — nunca uma cópia divergente. O
    # helper record_assertion_result (P1.1) também é o texto real do
    # gerador — nunca um stub que fingisse gravar.
    resolution = _resolve(schema)
    assert resolution.lines, "schema deveria gerar validação (sem $ref não suportado)"
    # P2.2 (assertions independentes): resolution.lines agora acumula em
    # _assertion_failures em vez de chamar pytest.fail() na hora — mesmo
    # padrão de agregação final de _generate_positive_success_test
    # reproduzido aqui, pra este wrapper continuar levantando
    # pytest.fail.Exception exatamente como antes quando a validação falha
    # (só que agora via o caminho adiado).
    source = (
        "import json\nimport os\nimport jsonschema\nimport pytest\n\n\n"
        + _RECORD_ASSERTION_RESULT_SOURCE
        + "\n\ndef _run(body):\n"
        + "    _assertion_failures = []\n"
        + "".join(resolution.lines)
        + "\n    if _assertion_failures:\n"
        '        pytest.fail("Assertion(s) reprovada(s): " + "; ".join(_assertion_failures))\n'
    )
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102 - texto do próprio gerador, não input externo
    return namespace["_run"]


def test_valid_body_passes():
    validator = _build_validator(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    )
    validator({"id": "abc"})  # nunca levanta


def test_invalid_body_fails_with_path_keyword_expected_and_message():
    import pytest

    validator = _build_validator(
        {"type": "object", "properties": {"age": {"type": "integer"}}, "required": ["age"]}
    )

    # pytest.fail levanta pytest.fail.Exception (alias estável e público
    # para o tipo interno usado por esse controle de fluxo do próprio
    # pytest) — nunca uma Exception comum.
    with pytest.raises(pytest.fail.Exception) as excinfo:
        validator({"age": "not-a-number"})

    message = str(excinfo.value)
    assert "caminho: age" in message
    assert "keyword: type" in message
    assert "esperado: 'integer'" in message
    assert "mensagem:" in message


def test_missing_required_field_fails_with_the_required_keyword():
    import pytest

    validator = _build_validator(
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    )

    with pytest.raises(pytest.fail.Exception) as excinfo:
        validator({})

    assert "keyword: required" in str(excinfo.value)


def test_local_ref_is_resolved_without_any_network_access():
    schema = {
        "type": "object",
        "properties": {"user": {"$ref": "#/$defs/User"}},
        "$defs": {
            "User": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            }
        },
    }
    validator = _build_validator(schema)

    validator({"user": {"id": "abc"}})  # nunca levanta, nunca acessa rede

    import pytest

    with pytest.raises(pytest.fail.Exception):
        validator({"user": {}})


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


def _content_type_assertion(content_type: str = "application/json") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.CONTENT_TYPE,
        description=f"Content-Type da resposta deve conter '{content_type}'.",
        expected_value=content_type,
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


# --- usa jsonschema, valida o body já parseado ------------------------------


def test_uses_the_jsonschema_library_on_the_already_parsed_body():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "import jsonschema" in generated.content
    assert "jsonschema.validate(instance=body, schema=_response_json_schema)" in (
        generated.content
    )
    # Nunca reparseia — usa a MESMA `body` da Parte 18.
    assert generated.content.count("json.loads(body_text)") == 1
    ast.parse(generated.content)


def test_schema_literal_is_embedded_verbatim_never_invented():
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "age": {"type": "integer", "minimum": 0}},
        "required": ["id"],
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert '"minimum": 0,' in generated.content
    assert '"required": [\n            "id",\n        ],' in generated.content


# --- schema válido passa / schema inválido falha com contexto --------------


def test_error_message_includes_path_keyword_expected_and_validator_message():
    schema = {"type": "object", "properties": {"age": {"type": "integer"}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "except jsonschema.exceptions.ValidationError as error:" in generated.content
    assert '+ "caminho: " + ".".join(str(part) for part in error.path)' in generated.content
    assert '+ "; keyword: " + str(error.validator)' in generated.content
    assert '+ "; esperado: " + repr(error.validator_value)' in generated.content
    assert '+ "; mensagem: " + error.message' in generated.content
    ast.parse(generated.content)


# --- $ref: local suportado, remoto reportado --------------------------------


def test_remote_ref_never_downloads_and_generates_a_warning_instead():
    schema = {
        "type": "object",
        "properties": {"user": {"$ref": "https://schemas.exemplo.com/user.json"}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "jsonschema.validate" not in generated.content
    assert "import jsonschema" not in generated.content
    assert len(generated.warnings) == 1
    warning = generated.warnings[0]
    assert warning.code == JSON_SCHEMA_REF_NOT_SUPPORTED
    assert "https://schemas.exemplo.com/user.json" in warning.message
    assert "não validado" in generated.content
    ast.parse(generated.content)


def test_local_ref_never_generates_a_warning():
    schema = {
        "type": "object",
        "properties": {"user": {"$ref": "#/$defs/User"}},
        "$defs": {"User": {"type": "object", "properties": {"id": {"type": "string"}}}},
    }
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "jsonschema.validate" in generated.content
    assert generated.warnings == ()
    ast.parse(generated.content)


def test_relative_file_ref_is_also_treated_as_unsupported():
    schema = {"type": "object", "properties": {"user": {"$ref": "./user.schema.json"}}}
    generated = _generate(_GET_USERS, _base_assertions(_schema_assertion(schema)))

    assert "jsonschema.validate" not in generated.content
    assert generated.warnings[0].code == JSON_SCHEMA_REF_NOT_SUPPORTED


# --- adicional, nunca substitutivo (mantém Partes 16-20) --------------------


def test_json_schema_never_replaces_the_earlier_assertions():
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }
    generated = _generate(
        _GET_USERS,
        (
            _status_assertion(),
            _content_type_assertion(),
            _valid_json_body_assertion(),
            _schema_assertion(schema),
        ),
    )

    assert "assert response.status == 200" in generated.content
    assert 'content_type.split(";")[0].strip().lower() == "application/json"' in generated.content
    assert "assert isinstance(body, dict)" in generated.content
    assert "_assert_required_field_present(body, ('id',), 'test_get_users_success'" in (
        generated.content
    )
    assert "_assert_field_type(body, ('id',), 'string', False, 'test_get_users_success'" in (
        generated.content
    )
    assert "jsonschema.validate" in generated.content
    ast.parse(generated.content)


# --- ausência de evidência: nunca inventa -----------------------------------


def test_no_schema_evidence_never_generates_json_schema_validation():
    generated = _generate(_GET_USERS, _base_assertions())

    assert "jsonschema" not in generated.content
    assert "JSON Schema:" not in generated.content
    # Sem nenhum schema, a Parte 18 já registra o warning dela própria
    # (estrutura do nível superior não determinada) — a Parte 21 nunca
    # acrescenta um warning quando simplesmente não há o que validar.
    assert all(w.code != JSON_SCHEMA_REF_NOT_SUPPORTED for w in generated.warnings)


def test_no_body_evidence_never_generates_json_schema_validation_even_with_schema():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(_GET_USERS, (_status_assertion(), _schema_assertion(schema)))

    assert "jsonschema" not in generated.content


# --- origem registrada ---------------------------------------------------


def test_json_schema_origin_is_recorded_in_the_docstring():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(
        _GET_USERS, _base_assertions(_schema_assertion(schema, origin="example"))
    )

    assert "JSON Schema: validado (origem: example)" in generated.content


# --- geração sintaticamente válida em todo cenário --------------------------


def test_all_json_schema_scenarios_produce_syntactically_valid_python():
    scenarios = (
        _base_assertions(),
        _base_assertions(_schema_assertion({"type": "object", "properties": {}})),
        _base_assertions(
            _schema_assertion(
                {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "age": {"type": "integer"}},
                    "required": ["id"],
                }
            )
        ),
        _base_assertions(
            _schema_assertion(
                {"type": "object", "properties": {"x": {"$ref": "https://exemplo.com/x.json"}}}
            )
        ),
        (_status_assertion(), _schema_assertion({"type": "object", "properties": {}})),
    )
    for assertions in scenarios:
        generated = _generate(_GET_USERS, assertions)
        ast.parse(generated.content)
