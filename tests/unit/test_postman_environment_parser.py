"""Parte 09 do plano de ação Playwright: PostmanEnvironmentParser — mesmo
formato de Environment do Postman já lido por NewmanAdapter, mas como
parser de domínio reaproveitável/testável.
"""

import json

import pytest

from api_quality_agent.domain.exceptions import InvalidPostmanEnvironmentError
from api_quality_agent.domain.models import InputOrigin, ResolvedInput
from api_quality_agent.parsers import PostmanEnvironmentParser

# Exemplo realista de export do Postman: campos extras (_postman_*, id)
# que o parser deve ignorar sem quebrar — "compatível com Postman" não é só
# aceitar o mínimo, é tolerar o que o Postman realmente exporta.
_REAL_POSTMAN_EXPORT = {
    "id": "b1f6e2b0-0000-4d1a-8a7a-000000000000",
    "name": "QA",
    "values": [
        {
            "key": "baseUrl",
            "value": "https://api.exemplo.com",
            "type": "default",
            "enabled": True,
        },
        {"key": "apiKey", "value": "segredo-123", "type": "secret", "enabled": True},
        {"key": "unused", "value": "x", "type": "default", "enabled": False},
    ],
    "_postman_variable_scope": "environment",
    "_postman_exported_at": "2026-08-06T12:00:00.000Z",
    "_postman_exported_using": "Postman/10.0.0",
}


def test_parses_a_realistic_postman_environment_export():
    env = PostmanEnvironmentParser().parse_text(json.dumps(_REAL_POSTMAN_EXPORT))

    assert env.name == "QA"
    assert len(env.variables) == 3


def test_get_returns_the_variable_by_key():
    env = PostmanEnvironmentParser().parse_text(json.dumps(_REAL_POSTMAN_EXPORT))

    variable = env.get("baseUrl")
    assert variable is not None
    assert variable.value == "https://api.exemplo.com"
    assert variable.is_secret is False


def test_get_marks_secret_typed_variables():
    env = PostmanEnvironmentParser().parse_text(json.dumps(_REAL_POSTMAN_EXPORT))

    variable = env.get("apiKey")
    assert variable is not None
    assert variable.is_secret is True


def test_get_never_resolves_disabled_variables():
    env = PostmanEnvironmentParser().parse_text(json.dumps(_REAL_POSTMAN_EXPORT))

    assert env.get("unused") is None


def test_get_returns_none_for_unknown_key():
    env = PostmanEnvironmentParser().parse_text(json.dumps(_REAL_POSTMAN_EXPORT))

    assert env.get("does-not-exist") is None


def test_parse_accepts_a_resolved_input_like_the_other_parsers():
    resolved = ResolvedInput(
        origin=InputOrigin.FILE,
        content_type="json",
        name="environment.json",
        content=json.dumps(_REAL_POSTMAN_EXPORT),
    )

    env = PostmanEnvironmentParser().parse(resolved)

    assert env.name == "QA"


# --- Erros claros -------------------------------------------------------


def test_invalid_json_raises_a_clear_error():
    with pytest.raises(InvalidPostmanEnvironmentError, match="não é um JSON válido"):
        PostmanEnvironmentParser().parse_text("{ isto não é json")


def test_non_object_root_raises_a_clear_error():
    with pytest.raises(InvalidPostmanEnvironmentError, match="objeto no nível raiz"):
        PostmanEnvironmentParser().parse_text("[1, 2, 3]")


def test_missing_values_raises_a_clear_error():
    with pytest.raises(InvalidPostmanEnvironmentError, match="'values'"):
        PostmanEnvironmentParser().parse_text(json.dumps({"name": "QA"}))


def test_values_not_a_list_raises_a_clear_error():
    with pytest.raises(InvalidPostmanEnvironmentError, match="'values'"):
        PostmanEnvironmentParser().parse_text(json.dumps({"name": "QA", "values": "oops"}))


# --- Robustez com entradas malformadas -----------------------------------


def test_malformed_entries_are_skipped_without_crashing():
    document = {
        "name": "QA",
        "values": [
            {"key": "ok", "value": "1", "type": "default", "enabled": True},
            "isto não é um objeto",
            {"value": "sem key"},
            {"key": "", "value": "key vazia"},
            42,
        ],
    }

    env = PostmanEnvironmentParser().parse_text(json.dumps(document))

    assert len(env.variables) == 1
    assert env.variables[0].key == "ok"


def test_environment_without_a_name_is_still_valid():
    env = PostmanEnvironmentParser().parse_text(json.dumps({"values": []}))

    assert env.name is None
    assert env.variables == ()
