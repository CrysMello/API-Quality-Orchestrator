"""Parte 07 do plano de ação Playwright: PlaywrightEndpointTestGenerator —
primeiro teste positivo real (GET simples), com fallback para o placeholder
(mais um warning) em qualquer caso ainda não suportado.
"""

import ast
import json

from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    ENDPOINT_NOT_SUPPORTED_YET,
    PlaywrightEndpointTestGenerator,
)
from api_quality_agent.parsers import PostmanCollectionParser


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


def _generate(request: dict, environment=None):
    strategy_source, normalized_request = _analyzed(request)
    from api_quality_agent.domain.models import TestStrategy

    strategy = TestStrategy(
        endpoint_source=strategy_source.source,
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy, normalized_request, environment
    )


# --- Caso simples suportado: GET, sem body, sem auth, sem variáveis --------


def test_simple_get_produces_a_real_positive_test():
    generated = _generate(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}
    )

    assert "def test_get_users_success(api_context):" in generated.content
    assert 'api_context.get("/users")' in generated.content
    assert "assert response is not None" in generated.content
    assert "@pytest.mark.skip" not in generated.content
    assert generated.scenario_names == ("success",)
    assert generated.warnings == ()


def test_function_name_matches_the_deterministic_slug_plus_success():
    generated = _generate(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/orders"}}
    )

    assert "def test_get_orders_success(" in generated.content


def test_docstring_contains_the_expected_fields():
    generated = _generate(
        {
            "name": "Listar usuários",
            "request": {"method": "GET", "url": "https://api.exemplo.com/users"},
        }
    )

    assert "Request: Listar usuários" in generated.content
    assert "Method: GET" in generated.content
    assert "Endpoint: GET /users" in generated.content
    assert "Scenario: success" in generated.content
    assert "Category: positive" in generated.content
    assert "Origin: NormalizedRequest" in generated.content


def test_generated_positive_content_is_syntactically_valid_python():
    generated = _generate(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}
    )

    ast.parse(generated.content)


def test_query_parameter_values_appear_structured_never_smashed_into_path_or_docstring():
    # Até a Parte 09, nenhuma query aparecia no conteúdo gerado (não era
    # processada). A partir da Parte 10 ela aparece de propósito — mas só
    # de forma estruturada via params=; nunca colada na URL/path nem no
    # campo "Endpoint:" da docstring (que é sempre só método+path,
    # strategy.endpoint_source, nunca query string — ver
    # _endpoint_source_label em api_analysis_engine.py).
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": "https://api.exemplo.com/login?api_key=valor-da-collection",
            }
        }
    )

    assert "Endpoint: GET /login\n" in generated.content
    assert "?api_key=" not in generated.content
    assert 'response = api_context.get("/login")' not in generated.content
    assert '"api_key": "valor-da-collection",' in generated.content


# --- Casos ainda não suportados: fallback + warning, nunca código enganoso ---


def test_non_get_method_falls_back_to_placeholder_with_warning():
    generated = _generate(
        {"request": {"method": "POST", "url": "https://api.exemplo.com/users"}}
    )

    assert "@pytest.mark.skip" in generated.content
    assert "response = api_context" not in generated.content
    assert len(generated.warnings) == 1
    warning = generated.warnings[0]
    assert warning.code == ENDPOINT_NOT_SUPPORTED_YET
    assert warning.endpoint == "POST /users"
    assert "método POST" in warning.message


def test_request_with_body_falls_back_to_placeholder_with_warning():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": "https://api.exemplo.com/users",
                "body": {"mode": "raw", "raw": '{"filter": "active"}'},
            }
        }
    )

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.warnings) == 1
    assert "body" in generated.warnings[0].message


def test_request_with_auth_falls_back_to_placeholder_with_warning():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": "https://api.exemplo.com/users",
                "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "abc123"}]},
            }
        }
    )

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.warnings) == 1
    assert "autenticação" in generated.warnings[0].message
    assert "abc123" not in generated.content


def test_request_with_postman_style_path_variable_falls_back_with_warning():
    generated = _generate(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/users/:id"}}
    )

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.warnings) == 1
    assert "variáveis" in generated.warnings[0].message


def test_request_with_openapi_style_path_variable_falls_back_with_warning():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": {
                    "raw": "https://api.exemplo.com/users/{id}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users", "{id}"],
                },
            }
        }
    )

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.warnings) == 1


def test_fallback_content_is_still_syntactically_valid_python():
    generated = _generate(
        {"request": {"method": "DELETE", "url": "https://api.exemplo.com/users/1"}}
    )

    ast.parse(generated.content)


def test_fallback_suggested_file_name_still_uses_deterministic_naming():
    generated = _generate(
        {"request": {"method": "POST", "url": "https://api.exemplo.com/users"}}
    )

    assert generated.suggested_file_name == "test_post_users.py"


# --- Parte 09: environment aceito, nunca vaza segredo no conteúdo ---------


def test_accepts_an_environment_without_using_its_values_yet():
    from api_quality_agent.domain.models import EnvironmentVariable, PostmanEnvironment

    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(
                key="apiKey", value="segredo-super-secreto", is_secret=True, enabled=True
            ),
        ),
    )

    generated = _generate(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}, environment
    )

    assert "def test_get_users_success(api_context):" in generated.content
    assert "segredo-super-secreto" not in generated.content
    assert "apiKey" not in generated.content


def test_environment_never_leaks_into_the_fallback_content_either():
    from api_quality_agent.domain.models import EnvironmentVariable, PostmanEnvironment

    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(
                key="token", value="outro-segredo-456", is_secret=True, enabled=True
            ),
        ),
    )

    generated = _generate(
        {"request": {"method": "POST", "url": "https://api.exemplo.com/users"}}, environment
    )

    assert "outro-segredo-456" not in generated.content
    assert "token" not in generated.content
    assert len(generated.warnings) == 1  # ainda cai no fallback normalmente


def test_generate_endpoint_still_works_without_an_environment_argument():
    # Compatibilidade: chamar generate_endpoint com só (strategy, request),
    # sem o terceiro argumento, continua funcionando (default None).
    strategy_source, normalized_request = _analyzed(
        {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}
    )
    from api_quality_agent.domain.models import TestStrategy

    strategy = TestStrategy(
        endpoint_source=strategy_source.source,
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)

    assert "def test_get_users_success(api_context):" in generated.content


# --- Parte 10: query parameters --------------------------------------------


def _request_with_query(query: list[dict], *, raw_suffix: str = "") -> dict:
    return {
        "request": {
            "method": "GET",
            "url": {
                "raw": f"https://api.exemplo.com/users{raw_suffix}",
                "protocol": "https",
                "host": ["api", "exemplo", "com"],
                "path": ["users"],
                "query": query,
            },
        }
    }


def test_no_query_parameters_keeps_the_single_line_call():
    generated = _generate({"request": {"method": "GET", "url": "https://api.exemplo.com/users"}})

    assert 'response = api_context.get("/users")' in generated.content
    assert "params=" not in generated.content


def test_a_single_query_parameter_uses_params_argument():
    generated = _generate(
        _request_with_query([{"key": "page", "value": "1"}], raw_suffix="?page=1")
    )

    assert (
        "    response = api_context.get(\n"
        '        "/users",\n'
        "        params={\n"
        '            "page": 1,\n'
        "        },\n"
        "    )\n"
    ) in generated.content
    ast.parse(generated.content)


def test_multiple_query_parameters_preserve_order_exactly_like_the_spec_example():
    generated = _generate(
        _request_with_query(
            [{"key": "page", "value": "1"}, {"key": "active", "value": "true"}],
            raw_suffix="?page=1&active=true",
        )
    )

    assert (
        "    response = api_context.get(\n"
        '        "/users",\n'
        "        params={\n"
        '            "page": 1,\n'
        '            "active": True,\n'
        "        },\n"
        "    )\n"
    ) in generated.content


def test_empty_value_is_preserved_as_an_explicit_empty_string():
    generated = _generate(
        _request_with_query([{"key": "search", "value": ""}], raw_suffix="?search=")
    )

    assert '"search": "",' in generated.content


def test_special_characters_do_not_break_the_generated_syntax():
    generated = _generate(
        _request_with_query(
            [{"key": "q", "value": 'a "quoted" \\ value with acentuação'}],
        )
    )

    ast.parse(generated.content)
    assert 'acentuação' in generated.content


def test_repeated_query_parameters_fall_back_with_a_specific_warning():
    generated = _generate(
        _request_with_query(
            [{"key": "tag", "value": "a"}, {"key": "tag", "value": "b"}],
            raw_suffix="?tag=a&tag=b",
        )
    )

    assert "@pytest.mark.skip" in generated.content
    assert "params=" not in generated.content
    assert len(generated.warnings) == 1
    assert "repetidos" in generated.warnings[0].message


def test_disabled_query_parameter_is_never_generated():
    generated = _generate(
        _request_with_query(
            [
                {"key": "page", "value": "1"},
                {"key": "debug", "value": "true", "disabled": True},
            ],
            raw_suffix="?page=1&debug=true",
        )
    )

    assert '"debug"' not in generated.content
    assert '"page": 1,' in generated.content


def test_only_disabled_query_parameters_keeps_the_single_line_call():
    generated = _generate(
        _request_with_query(
            [{"key": "debug", "value": "true", "disabled": True}], raw_suffix="?debug=true"
        )
    )

    assert 'response = api_context.get("/users")' in generated.content
    assert "params=" not in generated.content


def test_query_parameter_with_unresolved_variable_falls_back_with_warning():
    generated = _generate(
        _request_with_query(
            [{"key": "token", "value": "{{authToken}}"}], raw_suffix="?token={{authToken}}"
        )
    )

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.warnings) == 1
    assert "variáveis" in generated.warnings[0].message


# --- Coerção de tipo: conservadora, nunca inventa dado ----------------------


def test_numeric_value_becomes_a_python_int():
    generated = _generate(_request_with_query([{"key": "page", "value": "42"}]))
    assert '"page": 42,' in generated.content


def test_negative_numeric_value_becomes_a_python_int():
    generated = _generate(_request_with_query([{"key": "offset", "value": "-3"}]))
    assert '"offset": -3,' in generated.content


def test_boolean_literals_become_python_bool():
    generated = _generate(
        _request_with_query([{"key": "a", "value": "true"}, {"key": "b", "value": "false"}])
    )
    assert '"a": True,' in generated.content
    assert '"b": False,' in generated.content


def test_leading_zero_value_is_kept_as_string_not_coerced_to_int():
    # "007" != str(int("007")) == "7" — representação original pode ser
    # significativa (CEP, código), então não é convertida.
    generated = _generate(_request_with_query([{"key": "zip", "value": "007"}]))
    assert '"zip": "007",' in generated.content


def test_non_numeric_non_boolean_value_stays_a_string():
    generated = _generate(_request_with_query([{"key": "name", "value": "ana"}]))
    assert '"name": "ana",' in generated.content


def test_capitalized_true_is_not_coerced_and_stays_a_string():
    # Só "true"/"false" minúsculos (convenção HTTP/JSON) viram bool — "True"
    # capitalizado pode ser um valor de negócio real, não um booleano.
    generated = _generate(_request_with_query([{"key": "flag", "value": "True"}]))
    assert '"flag": "True",' in generated.content
