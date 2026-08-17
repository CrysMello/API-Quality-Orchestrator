"""Parte 07 do plano de ação Playwright: PlaywrightEndpointTestGenerator —
primeiro teste positivo real (GET simples), com fallback para o placeholder
(mais um warning) em qualquer caso ainda não suportado.
"""

import ast
import json

from api_quality_agent.domain.models import AssertionDefinition, AssertionType, TestStrategy
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    HTTP_METHOD_NOT_SUPPORTED,
    PlaywrightEndpointTestGenerator,
    REQUEST_BODY_NOT_RENDERED,
)
from api_quality_agent.parsers import PostmanCollectionParser

# Status 200/origem "contract" por padrão: a maioria dos testes deste arquivo
# não é sobre a Parte 16 (status HTTP) — sem isso, todo teste passaria a
# ganhar o warning EXPECTED_STATUS_NOT_DEFINED (nenhuma AssertionDefinition
# de STATUS_CODE presente) e a checar "assert response is not None" em vez
# de um assert de status, obscurecendo o que cada teste realmente verifica.
# Os testes da Parte 16 (seção dedicada abaixo) passam `assertions=()`
# explicitamente para exercitar o caminho "sem status confiável".
_DEFAULT_STATUS_ASSERTIONS = (
    AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description="Status code da resposta deve ser 200.",
        expected_value=200,
        origin="contract",
    ),
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


def _generate(request: dict, environment=None, *, assertions=_DEFAULT_STATUS_ASSERTIONS):
    strategy_source, normalized_request = _analyzed(request)

    strategy = TestStrategy(
        endpoint_source=strategy_source.source,
        assertions=assertions,
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
    # Parte 16: com status conhecido (default deste arquivo: 200/contract —
    # ver _DEFAULT_STATUS_ASSERTIONS), a asserção final é exata, não mais o
    # placeholder "assert response is not None".
    assert "assert response.status == 200" in generated.content
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


def test_unsupported_method_falls_back_to_placeholder_with_warning():
    # A partir da Parte 08A, GET/POST/PUT/PATCH/DELETE/HEAD (nativos) e
    # OPTIONS (via fetch) são suportados — TRACE fica fora da whitelist
    # explícita e continua caindo no fallback.
    generated = _generate(
        {"request": {"method": "TRACE", "url": "https://api.exemplo.com/users/1"}}
    )

    assert "@pytest.mark.skip" in generated.content
    assert "response = api_context" not in generated.content
    assert len(generated.warnings) == 1
    warning = generated.warnings[0]
    # Parte 24: código específico (nunca mais o genérico ENDPOINT_NOT_
    # SUPPORTED_YET) para o motivo exato do fallback.
    assert warning.code == HTTP_METHOD_NOT_SUPPORTED
    assert warning.endpoint == "TRACE /users/1"
    assert warning.method == "TRACE"
    assert warning.location == "method"
    assert "método TRACE" in warning.message


def test_post_without_body_produces_a_real_positive_test():
    # Parte 13: POST simples (sem body) também é suportado, não só quando
    # há um body JSON para carregar.
    generated = _generate(
        {"request": {"method": "POST", "url": "https://api.exemplo.com/users"}}
    )

    assert "def test_post_users_success(api_context):" in generated.content
    assert 'api_context.post("/users")' in generated.content
    assert "@pytest.mark.skip" not in generated.content
    assert generated.warnings == ()


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


def test_request_with_auth_literal_value_falls_back_to_placeholder_with_warning():
    # Até a Parte 11, qualquer autenticação estruturada caía no fallback.
    # A partir da Parte 12, Bearer/API Key/Basic passam a ser suportados
    # quando o valor é uma referência de variável ({{...}}) — um valor
    # literal hardcoded na Collection (como este) continua caindo no
    # fallback, agora com o código específico AUTHENTICATION_VALUE_NOT_
    # RESOLVED em vez do genérico ENDPOINT_NOT_SUPPORTED_YET.
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
    assert generated.warnings[0].code == "AUTHENTICATION_VALUE_NOT_RESOLVED"
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
        {"request": {"method": "TRACE", "url": "https://api.exemplo.com/users/1"}}
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
        {"request": {"method": "TRACE", "url": "https://api.exemplo.com/users/1"}}, environment
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


def test_query_parameter_with_pure_variable_reference_resolves_via_env_var():
    # A partir da Parte 15, uma referência PURA ({{nome}}, nada mais na
    # string) num query parameter resolve via o resolvedor central, em vez
    # de derrubar o endpoint inteiro para o fallback.
    generated = _generate(
        _request_with_query(
            [{"key": "token", "value": "{{authToken}}"}], raw_suffix="?token={{authToken}}"
        )
    )

    assert "@pytest.mark.skip" not in generated.content
    assert 'auth_token = os.environ.get("AQO_AUTH_TOKEN")' in generated.content
    assert '"token": auth_token,' in generated.content
    assert "{{authToken}}" not in generated.content
    assert generated.required_environment_variables == ("AQO_AUTH_TOKEN",)
    ast.parse(generated.content)


def test_query_parameter_with_partial_variable_falls_back_with_warning():
    # Só uma referência PURA resolve — "prefixo-{{authToken}}" continua sem
    # suporte, mesmo critério conservador de sempre.
    generated = _generate(
        _request_with_query(
            [{"key": "token", "value": "prefixo-{{authToken}}"}],
            raw_suffix="?token=prefixo-{{authToken}}",
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


# --- Parte 11: headers -------------------------------------------------------


def _request_with_headers(headers: list[dict]) -> dict:
    return {
        "request": {
            "method": "GET",
            "url": "https://api.exemplo.com/users",
            "header": headers,
        }
    }


def test_no_headers_keeps_the_call_without_a_headers_argument():
    generated = _generate({"request": {"method": "GET", "url": "https://api.exemplo.com/users"}})

    assert 'response = api_context.get("/users")' in generated.content
    assert "headers=" not in generated.content


def test_simple_headers_match_the_spec_example():
    generated = _generate(
        _request_with_headers(
            [
                {"key": "Accept", "value": "application/json"},
                {"key": "X-Correlation-Id", "value": "test-request"},
            ]
        )
    )

    assert (
        "    response = api_context.get(\n"
        '        "/users",\n'
        "        headers={\n"
        '            "Accept": "application/json",\n'
        '            "X-Correlation-Id": "test-request",\n'
        "        },\n"
        "    )\n"
    ) in generated.content
    ast.parse(generated.content)
    assert generated.warnings == ()


def test_disabled_header_is_never_generated():
    generated = _generate(
        _request_with_headers(
            [
                {"key": "Accept", "value": "application/json"},
                {"key": "X-Debug", "value": "on", "disabled": True},
            ]
        )
    )

    assert '"X-Debug"' not in generated.content
    assert '"Accept": "application/json",' in generated.content
    assert generated.warnings == ()  # desabilitado é decisão explícita, não gera warning


def test_empty_header_value_is_preserved():
    generated = _generate(_request_with_headers([{"key": "X-Trace", "value": ""}]))

    assert '"X-Trace": "",' in generated.content


def test_duplicate_header_same_case_keeps_the_last_value_with_warning():
    generated = _generate(
        _request_with_headers(
            [
                {"key": "X-Custom", "value": "first"},
                {"key": "X-Custom", "value": "second"},
            ]
        )
    )

    assert '"X-Custom": "second",' in generated.content
    assert generated.content.count('"X-Custom"') == 1
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == "DUPLICATE_HEADER_IGNORED"


def test_conflicting_header_different_case_resolves_to_a_single_header():
    generated = _generate(
        _request_with_headers(
            [
                {"key": "Accept", "value": "application/xml"},
                {"key": "accept", "value": "application/json"},
            ]
        )
    )

    # case-insensitive: as duas são o mesmo header HTTP — só uma sobrevive,
    # com o último valor definido.
    assert generated.content.lower().count('"accept"') == 1
    assert '"application/json",' in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == "DUPLICATE_HEADER_IGNORED"


def test_sensitive_header_authorization_is_never_written_literally():
    generated = _generate(
        _request_with_headers([{"key": "Authorization", "value": "Bearer super-secreto-token"}])
    )

    assert "super-secreto-token" not in generated.content
    assert "Authorization" not in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == "SENSITIVE_HEADER_OMITTED"


def test_sensitive_header_matching_environment_secret_is_omitted():
    from api_quality_agent.domain.models import EnvironmentVariable, PostmanEnvironment

    environment = PostmanEnvironment(
        name="QA",
        variables=(
            EnvironmentVariable(
                key="apiKey", value="valor-secreto-do-environment", is_secret=True, enabled=True
            ),
        ),
    )

    generated = _generate(
        _request_with_headers([{"key": "X-Api-Key", "value": "valor-secreto-do-environment"}]),
        environment,
    )

    assert "valor-secreto-do-environment" not in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == "SENSITIVE_HEADER_OMITTED"


def test_content_type_header_is_reserved_and_omitted():
    generated = _generate(
        _request_with_headers([{"key": "Content-Type", "value": "application/json"}])
    )

    assert "Content-Type" not in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == "RESERVED_HEADER_OMITTED"


def test_header_with_pure_variable_reference_resolves_via_env_var():
    # A partir da Parte 15, uma referência PURA num header resolve via o
    # resolvedor central, em vez de ser omitida.
    generated = _generate(
        _request_with_headers([{"key": "X-Tenant", "value": "{{tenantId}}"}])
    )

    assert 'tenant_id = os.environ.get("AQO_TENANT_ID")' in generated.content
    assert '"X-Tenant": tenant_id,' in generated.content
    assert "{{tenantId}}" not in generated.content
    assert generated.warnings == ()
    ast.parse(generated.content)


def test_header_with_partial_variable_is_omitted_with_warning():
    generated = _generate(
        _request_with_headers([{"key": "X-Tenant", "value": "prefixo-{{tenantId}}"}])
    )

    assert "headers=" not in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == "HEADER_VALUE_NOT_RESOLVED"


def test_headers_and_query_params_can_coexist_in_the_same_call():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": {
                    "raw": "https://api.exemplo.com/users?page=1",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users"],
                    "query": [{"key": "page", "value": "1"}],
                },
                "header": [{"key": "Accept", "value": "application/json"}],
            }
        }
    )

    assert "params={" in generated.content
    assert "headers={" in generated.content
    ast.parse(generated.content)


def test_header_order_is_deterministic_across_calls():
    request = _request_with_headers(
        [
            {"key": "Accept", "value": "application/json"},
            {"key": "X-Correlation-Id", "value": "abc"},
        ]
    )

    first = _generate(request).content
    second = _generate(request).content

    assert first == second


def test_endpoint_source_of_the_warning_matches_the_endpoint():
    generated = _generate(
        _request_with_headers([{"key": "Authorization", "value": "Bearer x"}])
    )

    assert generated.warnings[0].endpoint == "GET /users"


# --- Parte 12: autenticação ---------------------------------------------------


def _request_with_auth(auth: dict) -> dict:
    return {
        "request": {
            "method": "GET",
            "url": "https://api.exemplo.com/users",
            "auth": auth,
        }
    }


def _bearer_auth(token_value: str) -> dict:
    return {"type": "bearer", "bearer": [{"key": "token", "value": token_value}]}


def _api_key_auth(key_name: str, value: str, location: str | None = None) -> dict:
    entries = [{"key": "key", "value": key_name}, {"key": "value", "value": value}]
    if location is not None:
        entries.append({"key": "in", "value": location})
    return {"type": "apikey", "apikey": entries}


def _basic_auth(username: str, password: str) -> dict:
    return {
        "type": "basic",
        "basic": [{"key": "username", "value": username}, {"key": "password", "value": password}],
    }


# --- Bearer -------------------------------------------------------------------


def test_bearer_auth_uses_an_environment_variable():
    generated = _generate(_request_with_auth(_bearer_auth("{{accessToken}}")))

    assert 'token = os.environ.get("AQO_ACCESS_TOKEN")' in generated.content
    assert 'assert token, "Variável de ambiente obrigatória AQO_ACCESS_TOKEN não definida."' in (
        generated.content
    )
    assert '"Authorization": f"Bearer {token}",' in generated.content
    assert "import os" in generated.content
    assert generated.warnings == ()
    ast.parse(generated.content)


def test_bearer_auth_missing_token_param_is_not_supported():
    generated = _generate(_request_with_auth({"type": "bearer", "bearer": []}))

    assert "@pytest.mark.skip" in generated.content
    assert generated.warnings[0].code == "AUTHENTICATION_NOT_SUPPORTED"


def test_bearer_auth_with_literal_value_is_not_resolved():
    generated = _generate(_request_with_auth(_bearer_auth("literal-hardcoded-token")))

    assert "literal-hardcoded-token" not in generated.content
    assert generated.warnings[0].code == "AUTHENTICATION_VALUE_NOT_RESOLVED"


def test_bearer_auth_with_partial_variable_reference_is_not_resolved():
    # "Bearer {{accessToken}}" não é uma referência PURA — só um valor
    # {{...}} sozinho na string resolve.
    generated = _generate(_request_with_auth(_bearer_auth("Bearer {{accessToken}}")))

    assert generated.warnings[0].code == "AUTHENTICATION_VALUE_NOT_RESOLVED"


# --- API Key --------------------------------------------------------------


def test_api_key_in_header_is_inserted_in_the_correct_location():
    generated = _generate(
        _request_with_auth(_api_key_auth("X-API-Key", "{{apiKey}}", "header"))
    )

    assert 'api_key = os.environ.get("AQO_API_KEY")' in generated.content
    assert '"X-API-Key": api_key,' in generated.content
    assert "params={" not in generated.content
    assert generated.warnings == ()
    ast.parse(generated.content)


def test_api_key_in_query_is_inserted_in_the_correct_location():
    generated = _generate(_request_with_auth(_api_key_auth("api_key", "{{apiKey}}", "query")))

    assert 'api_key = os.environ.get("AQO_API_KEY")' in generated.content
    assert '"api_key": api_key,' in generated.content
    assert "params={" in generated.content
    assert "headers={" not in generated.content
    ast.parse(generated.content)


def test_api_key_without_explicit_location_defaults_to_header():
    generated = _generate(_request_with_auth(_api_key_auth("X-API-Key", "{{apiKey}}")))

    assert "headers={" in generated.content
    assert "params={" not in generated.content


def test_api_key_missing_value_is_not_supported():
    generated = _generate(
        _request_with_auth({"type": "apikey", "apikey": [{"key": "key", "value": "X-API-Key"}]})
    )

    assert generated.warnings[0].code == "AUTHENTICATION_NOT_SUPPORTED"


def test_api_key_with_invalid_location_is_not_supported():
    generated = _generate(
        _request_with_auth(_api_key_auth("X-API-Key", "{{apiKey}}", "cookie"))
    )

    assert generated.warnings[0].code == "AUTHENTICATION_NOT_SUPPORTED"


def test_api_key_variable_name_normalization_matches_the_spec_example():
    generated = _generate(_request_with_auth(_api_key_auth("X-API-Key", "{{apiKey}}", "header")))

    assert 'os.environ.get("AQO_API_KEY")' in generated.content


# --- Basic Auth -----------------------------------------------------------


def test_basic_auth_is_generated_only_when_complete():
    generated = _generate(
        _request_with_auth(_basic_auth("{{basicUsername}}", "{{basicPassword}}"))
    )

    assert 'username = os.environ.get("AQO_BASIC_USERNAME")' in generated.content
    assert 'password = os.environ.get("AQO_BASIC_PASSWORD")' in generated.content
    assert (
        'credentials = base64.b64encode(f"{username}:{password}".encode()).decode()'
        in generated.content
    )
    assert '"Authorization": f"Basic {credentials}",' in generated.content
    assert "import base64" in generated.content
    assert generated.warnings == ()
    ast.parse(generated.content)


def test_basic_auth_missing_password_is_not_supported():
    generated = _generate(
        _request_with_auth(
            {"type": "basic", "basic": [{"key": "username", "value": "{{basicUsername}}"}]}
        )
    )

    assert "@pytest.mark.skip" in generated.content
    assert generated.warnings[0].code == "AUTHENTICATION_NOT_SUPPORTED"


def test_basic_auth_with_literal_credentials_is_not_resolved():
    generated = _generate(_request_with_auth(_basic_auth("admin", "hunter2")))

    assert "hunter2" not in generated.content
    assert "admin" not in generated.content
    assert generated.warnings[0].code == "AUTHENTICATION_VALUE_NOT_RESOLVED"


# --- Tipo não suportado / desconhecido -------------------------------------


def test_unsupported_auth_type_produces_a_warning():
    generated = _generate(
        _request_with_auth({"type": "oauth2", "oauth2": [{"key": "accessToken", "value": "x"}]})
    )

    assert "@pytest.mark.skip" in generated.content
    assert generated.warnings[0].code == "AUTHENTICATION_NOT_SUPPORTED"
    assert "oauth2" in generated.warnings[0].message


def test_other_recognized_but_unsupported_auth_types_all_produce_a_warning():
    # Todos os demais tipos que o Postman reconhece e este projeto ainda
    # não suporta (digest, awsv4, hawk, ntlm, edgegrid) — não só oauth2.
    for auth_type in ("digest", "awsv4", "hawk", "ntlm", "edgegrid"):
        generated = _generate(_request_with_auth({"type": auth_type, auth_type: []}))
        assert generated.warnings[0].code == "AUTHENTICATION_NOT_SUPPORTED", auth_type
        assert "@pytest.mark.skip" in generated.content, auth_type


# --- Proteção contra duplicação de Authorization ---------------------------


def test_manual_authorization_header_never_duplicates_the_auth_derived_one():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": "https://api.exemplo.com/users",
                "auth": _bearer_auth("{{accessToken}}"),
                "header": [{"key": "Authorization", "value": "Bearer valor-manual-antigo"}],
            }
        }
    )

    assert generated.content.count('"Authorization"') == 1
    assert '"Authorization": f"Bearer {token}",' in generated.content
    assert "valor-manual-antigo" not in generated.content
    # dois warnings: um por excluir o header manual (Parte 11), outro nenhum
    # da autenticação (ela foi resolvida com sucesso) — só o primeiro.
    codes = {warning.code for warning in generated.warnings}
    assert "SENSITIVE_HEADER_OMITTED" in codes
    ast.parse(generated.content)


# --- Nunca inventa autenticação ausente ------------------------------------


def test_no_auth_block_produces_no_preamble_and_no_authorization_header():
    generated = _generate({"request": {"method": "GET", "url": "https://api.exemplo.com/users"}})

    assert "Authorization" not in generated.content
    assert "os.environ" not in generated.content
    assert "sem autenticação" in generated.content


# --- Parte 14: multipart/form-data ------------------------------------------


def _request_with_multipart(fields: list[dict]) -> dict:
    return {
        "request": {
            "method": "POST",
            "url": "https://api.exemplo.com/upload",
            "body": {"mode": "formdata", "formdata": fields},
        }
    }


def test_multipart_with_only_text_fields():
    generated = _generate(
        _request_with_multipart(
            [
                {"key": "name", "value": "Rex", "type": "text"},
                {"key": "species", "value": "dog", "type": "text"},
            ]
        )
    )

    assert "@pytest.mark.skip" not in generated.content
    assert (
        "        multipart={\n"
        '            "name": "Rex",\n'
        '            "species": "dog",\n'
        "        },\n"
    ) in generated.content
    assert "data=" not in generated.content
    ast.parse(generated.content)
    assert generated.warnings == ()


def test_multipart_with_only_a_file_field():
    generated = _generate(
        _request_with_multipart(
            [{"key": "avatar", "type": "file", "src": "/home/joao/Desktop/avatar.png"}]
        )
    )

    # Nunca o env var derivado do "src" da Collection — sempre do "key".
    assert 'avatar_path = os.environ.get("AQO_UPLOAD_AVATAR")' in generated.content
    assert (
        'assert avatar_path, "Variável de ambiente obrigatória AQO_UPLOAD_AVATAR não definida."'
        in generated.content
    )
    assert "if not os.path.isfile(avatar_path):" in generated.content
    assert 'with open(avatar_path, "rb") as avatar_fh:' in generated.content
    assert "avatar_buffer = avatar_fh.read()" in generated.content
    assert '"avatar": {' in generated.content
    assert '"name": os.path.basename(avatar_path),' in generated.content
    assert "mimetypes.guess_type(avatar_path)[0]" in generated.content
    assert '"buffer": avatar_buffer,' in generated.content
    assert "import mimetypes" in generated.content
    assert "import os" in generated.content
    assert "import pytest" in generated.content
    # Nunca o caminho local (sensível/específico da máquina de quem criou a
    # Collection) persistido no código gerado.
    assert "/home/joao/Desktop/avatar.png" not in generated.content
    ast.parse(generated.content)
    assert generated.warnings == ()


def test_multipart_with_fields_and_a_file():
    generated = _generate(
        _request_with_multipart(
            [
                {"key": "name", "value": "Rex", "type": "text"},
                {"key": "avatar", "type": "file", "src": "/tmp/avatar.png"},
            ]
        )
    )

    assert '"name": "Rex",' in generated.content
    assert '"avatar": {' in generated.content
    ast.parse(generated.content)


def test_multipart_missing_file_fails_at_runtime_with_a_clear_message():
    # "Validar existência do arquivo em runtime" + "Gerar mensagem clara
    # quando o arquivo obrigatório estiver ausente" — checado só quando o
    # teste roda de verdade, nunca na geração (o caminho apontado por
    # AQO_UPLOAD_CONTRACT pode nem existir na máquina onde a suíte roda).
    generated = _generate(
        _request_with_multipart([{"key": "contract", "type": "file", "src": "/tmp/contract.pdf"}])
    )

    assert "if not os.path.isfile(contract_path):" in generated.content
    assert (
        "pytest.fail("
        '"Arquivo obrigatório não encontrado para o campo \'contract\': " + contract_path)'
    ) in generated.content
    ast.parse(generated.content)


def test_multipart_text_field_with_pure_variable_reference_resolves_via_env_var():
    generated = _generate(
        _request_with_multipart([{"key": "note", "value": "{{comment}}", "type": "text"}])
    )

    assert 'comment = os.environ.get("AQO_COMMENT")' in generated.content
    assert (
        'assert comment, "Variável de ambiente obrigatória AQO_COMMENT não definida."'
        in generated.content
    )
    assert '"note": comment,' in generated.content
    assert "{{comment}}" not in generated.content
    ast.parse(generated.content)
    assert generated.warnings == ()


def test_multipart_text_field_with_partial_variable_is_kept_as_literal_text():
    # Só uma referência PURA ({{nome}}, nada mais na string) resolve — mesmo
    # critério conservador já usado pela autenticação (Parte 12).
    generated = _generate(
        _request_with_multipart(
            [{"key": "note", "value": "prefixo-{{comment}}", "type": "text"}]
        )
    )

    assert '"note": "prefixo-{{comment}}",' in generated.content
    assert "os.environ" not in generated.content


def test_multipart_file_field_without_key_falls_back_with_a_specific_warning():
    generated = _generate(
        _request_with_multipart([{"type": "file", "src": "/tmp/sem-nome.png"}])
    )

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == "MULTIPART_FILE_NOT_RESOLVED"
    ast.parse(generated.content)


def test_disabled_multipart_field_is_never_rendered():
    generated = _generate(
        _request_with_multipart(
            [
                {"key": "name", "value": "Rex", "type": "text"},
                {"key": "debug", "value": "true", "type": "text", "disabled": True},
            ]
        )
    )

    assert '"name": "Rex",' in generated.content
    assert '"debug"' not in generated.content


def test_disabled_file_field_without_key_never_blocks_generation():
    # Campo desabilitado é decisão explícita já tomada na Collection — sua
    # "key" ausente nunca deveria impedir a geração do endpoint.
    generated = _generate(
        _request_with_multipart(
            [
                {"key": "name", "value": "Rex", "type": "text"},
                {"type": "file", "disabled": True},
            ]
        )
    )

    assert "@pytest.mark.skip" not in generated.content
    assert '"name": "Rex",' in generated.content


def test_multipart_never_emits_a_manual_boundary_or_content_type_header():
    generated = _generate(
        {
            "request": {
                "method": "POST",
                "url": "https://api.exemplo.com/upload",
                "header": [
                    {"key": "Content-Type", "value": "multipart/form-data; boundary=custom"}
                ],
                "body": {
                    "mode": "formdata",
                    "formdata": [{"key": "name", "value": "Rex", "type": "text"}],
                },
            }
        }
    )

    assert "boundary" not in generated.content
    assert "Content-Type" not in generated.content


def test_multipart_content_is_deterministic_across_calls():
    request = _request_with_multipart(
        [
            {"key": "name", "value": "Rex", "type": "text"},
            {"key": "avatar", "type": "file", "src": "/tmp/avatar.png"},
        ]
    )

    first = _generate(request).content
    second = _generate(request).content

    assert first == second


# --- Parte 15: resolvedor central de variáveis ------------------------------


def _env(**variables):
    from api_quality_agent.domain.models import EnvironmentVariable, PostmanEnvironment

    return PostmanEnvironment(
        name="QA",
        variables=tuple(
            EnvironmentVariable(key=key, value=value, is_secret=is_secret, enabled=True)
            for key, (value, is_secret) in variables.items()
        ),
    )


def _request_with_host_and_path(
    host: list[str], path: list[str], *, variables: list[dict] | None = None
) -> dict:
    return {
        "request": {
            "method": "GET",
            "url": {
                "raw": "https://" + ".".join(host) + "/" + "/".join(path),
                "protocol": "https",
                "host": host,
                "path": path,
                "variable": variables or [],
            },
        }
    }


# --- base URL -----------------------------------------------------------


def test_base_url_variable_resolves_via_environment():
    generated = _generate(
        _request_with_host_and_path(["{{baseUrl}}"], ["users"]),
        _env(baseUrl=("api.staging.exemplo.com", False)),
    )

    assert "@pytest.mark.skip" not in generated.content
    assert generated.base_url == "https://api.staging.exemplo.com"
    assert generated.resolved_variables == (("baseUrl", "api.staging.exemplo.com"),)
    ast.parse(generated.content)


def test_base_url_variable_without_environment_falls_back_with_unresolved_variable():
    generated = _generate(_request_with_host_and_path(["{{baseUrl}}"], ["users"]))

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.unresolved_variables) == 1
    assert generated.unresolved_variables[0].name == "baseUrl"
    assert generated.unresolved_variables[0].location == "base_url"


def test_base_url_variable_never_resolves_from_a_secret():
    generated = _generate(
        _request_with_host_and_path(["{{baseUrl}}"], ["users"]),
        _env(baseUrl=("api.staging.exemplo.com", True)),
    )

    assert "@pytest.mark.skip" in generated.content
    assert generated.unresolved_variables[0].name == "baseUrl"


# --- path ------------------------------------------------------------------


def test_double_brace_path_variable_resolves_via_environment():
    generated = _generate(
        _request_with_host_and_path(["api", "exemplo", "com"], ["{{version}}", "users"]),
        _env(version=("v2", False)),
    )

    assert "@pytest.mark.skip" not in generated.content
    assert 'api_context.get("/v2/users")' in generated.content
    assert generated.resolved_variables == (("version", "v2"),)
    ast.parse(generated.content)


def test_openapi_style_path_variable_resolves_via_collection_default():
    generated = _generate(
        _request_with_host_and_path(
            ["api", "exemplo", "com"],
            ["users", "{id}"],
            variables=[{"key": "id", "value": "42"}],
        )
    )

    assert "@pytest.mark.skip" not in generated.content
    assert 'api_context.get("/users/42")' in generated.content
    assert generated.resolved_variables == (("id", "42"),)
    assert generated.required_environment_variables == ()
    ast.parse(generated.content)


def test_postman_style_path_variable_resolves_via_collection_default():
    generated = _generate(
        _request_with_host_and_path(
            ["api", "exemplo", "com"], ["users", ":id"], variables=[{"key": "id", "value": "7"}]
        )
    )

    assert "@pytest.mark.skip" not in generated.content
    assert 'api_context.get("/users/7")' in generated.content


def test_path_variable_without_collection_default_falls_back_with_unresolved_variable():
    # "produzida por outro teste" (fora de escopo desta fase) — sem default
    # na Collection, o segmento continua não resolvido.
    generated = _generate(
        _request_with_host_and_path(
            ["api", "exemplo", "com"], ["users", "{id}"], variables=[{"key": "id", "value": ""}]
        )
    )

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.unresolved_variables) == 1
    assert generated.unresolved_variables[0].name == "id"
    assert generated.unresolved_variables[0].location == "path"


def test_path_variable_never_deferred_to_a_system_environment_variable():
    # Limitação deliberada desta parte: path/base URL só resolvem via
    # Environment/Collection (prioridades 1/2), nunca via AQO_* em runtime
    # (prioridade 3) — mesmo com um Environment informado, sem valor para
    # "id" ali, o segmento fica sem resolução.
    generated = _generate(
        _request_with_host_and_path(
            ["api", "exemplo", "com"], ["users", "{id}"], variables=[{"key": "id", "value": ""}]
        ),
        _env(other=("x", False)),
    )

    assert "@pytest.mark.skip" in generated.content
    assert "AQO_ID" not in generated.content


# --- query e headers ---------------------------------------------------------


def test_query_parameter_with_pure_variable_resolves_via_environment_as_a_literal():
    generated = _generate(
        _request_with_query([{"key": "region", "value": "{{region}}"}]),
        _env(region=("us-east-1", False)),
    )

    assert "@pytest.mark.skip" not in generated.content
    assert '"region": "us-east-1",' in generated.content
    assert "os.environ" not in generated.content
    assert generated.required_environment_variables == ()
    ast.parse(generated.content)


def test_header_with_pure_variable_resolves_via_environment_as_a_literal():
    generated = _generate(
        _request_with_headers([{"key": "X-Tenant", "value": "{{tenantId}}"}]),
        _env(tenantId=("tenant-42", False)),
    )

    assert '"X-Tenant": "tenant-42",' in generated.content
    assert "os.environ" not in generated.content
    ast.parse(generated.content)


def test_same_variable_used_in_header_and_query_shares_a_single_local_variable():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": {
                    "raw": "https://api.exemplo.com/users?region={{region}}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users"],
                    "query": [{"key": "region", "value": "{{region}}"}],
                },
                "header": [{"key": "X-Region", "value": "{{region}}"}],
            }
        }
    )

    assert generated.content.count('region = os.environ.get("AQO_REGION")') == 1
    assert '"region": region,' in generated.content
    assert '"X-Region": region,' in generated.content
    assert generated.required_environment_variables == ("AQO_REGION",)
    ast.parse(generated.content)


# --- autenticação ------------------------------------------------------------


def test_bearer_auth_embeds_a_non_secret_environment_value_as_a_literal():
    generated = _generate(
        _request_with_auth(_bearer_auth("{{accessToken}}")),
        _env(accessToken=("literal-token-value", False)),
    )

    assert 'token = "literal-token-value"' in generated.content
    assert "os.environ" not in generated.content
    assert '"Authorization": f"Bearer {token}",' in generated.content
    assert generated.required_environment_variables == ()
    assert generated.resolved_variables == (("accessToken", "literal-token-value"),)
    ast.parse(generated.content)


def test_bearer_auth_still_defers_a_secret_environment_value():
    generated = _generate(
        _request_with_auth(_bearer_auth("{{accessToken}}")),
        _env(accessToken=("super-secreto", True)),
    )

    assert "super-secreto" not in generated.content
    assert 'token = os.environ.get("AQO_ACCESS_TOKEN")' in generated.content
    assert generated.required_environment_variables == ("AQO_ACCESS_TOKEN",)
    assert generated.resolved_variables == ()


def test_basic_auth_with_environment_values_embeds_both_as_literals():
    generated = _generate(
        _request_with_auth(_basic_auth("{{basicUsername}}", "{{basicPassword}}")),
        _env(basicUsername=("admin", False), basicPassword=("hunter2", False)),
    )

    assert 'username = "admin"' in generated.content
    assert 'password = "hunter2"' in generated.content
    assert (
        'credentials = base64.b64encode(f"{username}:{password}".encode()).decode()'
        in generated.content
    )
    assert generated.required_environment_variables == ()
    ast.parse(generated.content)


# --- body JSON e multipart textual ------------------------------------------


def test_json_body_pure_variable_value_resolves_via_environment():
    generated = _generate(
        {
            "request": {
                "method": "POST",
                "url": "https://api.exemplo.com/users",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": {"mode": "raw", "raw": '{"region": "{{region}}"}'},
            }
        },
        _env(region=("us-east-1", False)),
    )

    assert '"region": "us-east-1",' in generated.content
    assert "os.environ" not in generated.content
    ast.parse(generated.content)


def test_multipart_text_field_pure_variable_resolves_via_environment():
    generated = _generate(
        _request_with_multipart([{"key": "note", "value": "{{comment}}", "type": "text"}]),
        _env(comment=("hello", False)),
    )

    assert '"note": "hello",' in generated.content
    assert "os.environ" not in generated.content
    ast.parse(generated.content)


# --- rastreabilidade agregada (required_environment_variables) -------------


def test_required_environment_variables_are_deduplicated_and_sorted():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": {
                    "raw": "https://api.exemplo.com/users?a={{alpha}}",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users"],
                    "query": [{"key": "a", "value": "{{alpha}}"}],
                },
                "header": [{"key": "X-Beta", "value": "{{beta}}"}],
            }
        }
    )

    assert generated.required_environment_variables == ("AQO_ALPHA", "AQO_BETA")


# --- Parte 08A: PUT/PATCH/DELETE/HEAD/OPTIONS --------------------------------
# Nenhum renderer independente por método — todos reaproveitam a mesma
# montagem de URL/params/headers/auth/data/multipart e o mesmo pipeline de
# asserções já exercitado acima para GET/POST; só o verbo (e, para OPTIONS,
# fetch(..., method=...) em vez de um método nativo) muda.


def test_simple_put_produces_a_real_positive_test():
    generated = _generate({"request": {"method": "PUT", "url": "https://api.exemplo.com/users/1"}})

    assert "def test_put_users_1_success(api_context):" in generated.content
    assert 'api_context.put("/users/1")' in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_simple_patch_produces_a_real_positive_test():
    generated = _generate(
        {"request": {"method": "PATCH", "url": "https://api.exemplo.com/users/1"}}
    )

    assert "def test_patch_users_1_success(api_context):" in generated.content
    assert 'api_context.patch("/users/1")' in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_simple_delete_produces_a_real_positive_test():
    generated = _generate(
        {"request": {"method": "DELETE", "url": "https://api.exemplo.com/users/1"}}
    )

    assert "def test_delete_users_1_success(api_context):" in generated.content
    assert 'api_context.delete("/users/1")' in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_simple_head_produces_a_real_positive_test():
    generated = _generate({"request": {"method": "HEAD", "url": "https://api.exemplo.com/users"}})

    assert "def test_head_users_success(api_context):" in generated.content
    assert 'api_context.head("/users")' in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_simple_options_uses_fetch_with_an_explicit_method_kwarg():
    # OPTIONS não tem um método nativo dedicado em APIRequestContext — é
    # servido via fetch(..., method="OPTIONS"), nunca api_context.options(...)
    # (que não existe) nem fetch() sem o argumento method= explícito.
    generated = _generate(
        {"request": {"method": "OPTIONS", "url": "https://api.exemplo.com/status"}}
    )

    assert "def test_options_status_success(api_context):" in generated.content
    assert (
        "    response = api_context.fetch(\n"
        '        "/status",\n'
        '        method="OPTIONS",\n'
        "    )\n"
    ) in generated.content
    assert "api_context.options" not in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_put_with_json_body_produces_a_real_positive_test():
    generated = _generate(
        {
            "request": {
                "method": "PUT",
                "url": "https://api.exemplo.com/users/1",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": {"mode": "raw", "raw": '{"name": "Ana"}'},
            }
        }
    )

    assert 'api_context.put(\n' in generated.content
    assert '    request_body = {\n        "name": "Ana",\n    }\n' in generated.content
    assert "data=request_body,\n" in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_patch_with_headers_auth_and_body_produces_a_real_positive_test():
    generated = _generate(
        {
            "request": {
                "method": "PATCH",
                "url": "https://api.exemplo.com/users/1",
                "header": [
                    {"key": "Content-Type", "value": "application/json"},
                    {"key": "X-Correlation-Id", "value": "abc-123"},
                ],
                "auth": {
                    "type": "bearer",
                    "bearer": [{"key": "token", "value": "{{accessToken}}"}],
                },
                "body": {"mode": "raw", "raw": '{"status": "active"}'},
            }
        },
        _env(accessToken=("secret-token-value", True)),
    )

    assert "def test_patch_users_1_success(api_context):" in generated.content
    assert "response = api_context.patch(\n" in generated.content
    assert '"X-Correlation-Id": "abc-123",' in generated.content
    assert '"Authorization": f"Bearer {token}",' in generated.content
    assert "request_body = {" in generated.content
    assert "data=request_body,\n" in generated.content
    assert "secret-token-value" not in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_delete_with_query_parameters_produces_a_real_positive_test():
    generated = _generate(
        {
            "request": {
                "method": "DELETE",
                "url": {
                    "raw": "https://api.exemplo.com/users?soft=true",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users"],
                    "query": [{"key": "soft", "value": "true"}],
                },
            }
        }
    )

    assert "response = api_context.delete(\n" in generated.content
    assert '"soft": True,' in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_delete_with_json_body_is_supported_when_the_request_really_has_a_body():
    # "Não assumir que somente POST/PUT/PATCH podem possuir body": a
    # presença de body é decidida pelo NormalizedRequest (has_content),
    # nunca pelo verbo — DELETE com body é tão suportado quanto sem.
    generated = _generate(
        {
            "request": {
                "method": "DELETE",
                "url": "https://api.exemplo.com/users/1",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": {"mode": "raw", "raw": '{"reason": "duplicate"}'},
            }
        }
    )

    assert "response = api_context.delete(\n" in generated.content
    assert '    request_body = {\n        "reason": "duplicate",\n    }\n' in generated.content
    assert "data=request_body,\n" in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_options_with_headers_and_query_still_uses_fetch_with_method_kwarg():
    generated = _generate(
        {
            "request": {
                "method": "OPTIONS",
                "url": {
                    "raw": "https://api.exemplo.com/users?scope=admin",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users"],
                    "query": [{"key": "scope", "value": "admin"}],
                },
                "header": [{"key": "Accept", "value": "application/json"}],
            }
        }
    )

    assert (
        "    response = api_context.fetch(\n"
        '        "/users",\n'
        '        method="OPTIONS",\n'
        "        params={\n"
        '            "scope": "admin",\n'
        "        },\n"
        "        headers={\n"
        '            "Accept": "application/json",\n'
        "        },\n"
        "    )\n"
    ) in generated.content
    assert "@pytest.mark.skip" not in generated.content
    ast.parse(generated.content)


def test_head_does_not_attempt_to_parse_a_body_without_evidence():
    # "Para HEAD, não gerar parsing ou validações de body quando não houver
    # expectativa explícita de conteúdo": mesma regra genérica das Partes
    # 17/18 (parse só com evidência de Content-Type/JSON), sem tratamento
    # especial de método — este teste comprova o comportamento resultante.
    generated = _generate({"request": {"method": "HEAD", "url": "https://api.exemplo.com/users"}})

    assert "response.text()" not in generated.content
    assert "json.loads" not in generated.content
    assert "body = " not in generated.content
    ast.parse(generated.content)


def test_response_204_preserves_the_no_content_rule_for_a_newly_supported_method():
    # Regra já existente (Parte 16/18) continua valendo para os métodos
    # recém-suportados: 204 documentado não gera parsing/validação de body.
    generated = _generate(
        {"request": {"method": "DELETE", "url": "https://api.exemplo.com/users/1"}},
        assertions=(
            AssertionDefinition(
                assertion_type=AssertionType.STATUS_CODE,
                description="Status code da resposta deve ser 204.",
                expected_value=204,
                origin="contract",
            ),
        ),
    )

    assert "assert response.status == 204" in generated.content
    assert "response.text()" not in generated.content
    assert "json.loads" not in generated.content
    ast.parse(generated.content)


# --- Parte 08B: payload por método, reutilizando o mesmo renderer ------------
# "if normalized_request.body is not None: render_body()" — nenhuma destas
# regras é implementada de novo por método; todas exercitam o MESMO
# _resolve_body/_render_json_literal já usado desde as Partes 13/14/15, só
# variando o verbo (e, por consequência, api_context.<verbo> ou
# fetch(method=...)) e o payload em si.


def _json_body_request(method: str, url: str, payload) -> dict:
    return {
        "request": {
            "method": method,
            "url": url,
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": {"mode": "raw", "raw": json.dumps(payload)},
        }
    }


# --- POST -----------------------------------------------------------------


def test_post_json_object_body_is_rendered():
    generated = _generate(_json_body_request("POST", "https://api.exemplo.com/users", {"name": "Maria"}))

    assert '    request_body = {\n        "name": "Maria",\n    }\n' in generated.content
    assert "response = api_context.post(\n" in generated.content
    assert "data=request_body,\n" in generated.content
    ast.parse(generated.content)


def test_post_json_array_body_is_not_converted_into_an_object():
    generated = _generate(
        _json_body_request("POST", "https://api.exemplo.com/batch", [{"id": 1}, {"id": 2}])
    )

    assert (
        "    request_body = [\n"
        "        {\n"
        '            "id": 1,\n'
        "        },\n"
        "        {\n"
        '            "id": 2,\n'
        "        },\n"
        "    ]\n"
    ) in generated.content
    assert "data=request_body,\n" in generated.content
    ast.parse(generated.content)


def test_post_without_body_never_emits_a_data_or_multipart_argument():
    generated = _generate({"request": {"method": "POST", "url": "https://api.exemplo.com/users"}})

    assert 'response = api_context.post("/users")' in generated.content
    assert "data=" not in generated.content
    assert "multipart=" not in generated.content


def test_post_multipart_uses_the_same_multipart_renderer():
    generated = _generate(
        {
            "request": {
                "method": "POST",
                "url": "https://api.exemplo.com/upload",
                "body": {
                    "mode": "formdata",
                    "formdata": [{"key": "name", "value": "Rex", "type": "text"}],
                },
            }
        }
    )

    assert "response = api_context.post(\n" in generated.content
    assert '"name": "Rex",' in generated.content
    assert "multipart={\n" in generated.content
    assert "data=" not in generated.content
    ast.parse(generated.content)


# --- PUT --------------------------------------------------------------------


def test_put_nested_object_preserves_the_full_hierarchy():
    generated = _generate(
        _json_body_request(
            "PUT",
            "https://api.exemplo.com/orders/1",
            {"customer": {"name": "Maria", "address": {"city": "Brasília"}}},
        )
    )

    assert (
        "    request_body = {\n"
        '        "customer": {\n'
        '            "name": "Maria",\n'
        '            "address": {\n'
        '                "city": "Brasília",\n'
        "            },\n"
        "        },\n"
        "    }\n"
    ) in generated.content
    ast.parse(generated.content)


def test_put_array_body_is_not_converted_into_an_object():
    generated = _generate(
        _json_body_request("PUT", "https://api.exemplo.com/orders/1/items", ["a", "b"])
    )

    assert '    request_body = [\n        "a",\n        "b",\n    ]\n' in generated.content
    assert "response = api_context.put(\n" in generated.content
    ast.parse(generated.content)


def test_put_without_body_never_emits_a_data_argument():
    generated = _generate({"request": {"method": "PUT", "url": "https://api.exemplo.com/users/1"}})

    assert 'response = api_context.put("/users/1")' in generated.content
    assert "data=" not in generated.content


def test_put_with_query_and_body_renders_both():
    generated = _generate(
        {
            "request": {
                "method": "PUT",
                "url": {
                    "raw": "https://api.exemplo.com/users/10?notify=true",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users", "10"],
                    "query": [{"key": "notify", "value": "true"}],
                },
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": {"mode": "raw", "raw": '{"active": true}'},
            }
        }
    )

    assert '"notify": True,' in generated.content
    assert '    request_body = {\n        "active": True,\n    }\n' in generated.content
    assert "response = api_context.put(\n" in generated.content
    ast.parse(generated.content)


def test_put_with_auth_and_body_renders_both():
    generated = _generate(
        {
            "request": {
                "method": "PUT",
                "url": "https://api.exemplo.com/users/10",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "auth": {
                    "type": "bearer",
                    "bearer": [{"key": "token", "value": "{{accessToken}}"}],
                },
                "body": {"mode": "raw", "raw": '{"active": true}'},
            }
        },
        _env(accessToken=("token-value", False)),
    )

    assert '"Authorization": f"Bearer {token}",' in generated.content
    assert '    request_body = {\n        "active": True,\n    }\n' in generated.content
    assert "response = api_context.put(\n" in generated.content
    ast.parse(generated.content)


# --- PATCH --------------------------------------------------------------------


def test_patch_partial_object_never_adds_the_other_fields():
    # Regra principal desta parte: PATCH transporta exatamente o que o
    # NormalizedRequest tem, nunca o recurso inteiro reconstruído — só
    # "active" aparece, nunca "name"/"email"/outros campos do recurso.
    generated = _generate(_json_body_request("PATCH", "https://api.exemplo.com/users/10", {"active": False}))

    assert '    request_body = {\n        "active": False,\n    }\n' in generated.content
    assert '"name"' not in generated.content
    assert '"email"' not in generated.content
    assert "response = api_context.patch(\n" in generated.content
    ast.parse(generated.content)


def test_patch_null_value_is_preserved_as_none_never_removed():
    generated = _generate(
        _json_body_request("PATCH", "https://api.exemplo.com/users/10", {"middleName": None})
    )

    assert '    request_body = {\n        "middleName": None,\n    }\n' in generated.content
    ast.parse(generated.content)


def test_patch_boolean_value_is_preserved_as_python_bool_never_a_string():
    generated = _generate(
        _json_body_request("PATCH", "https://api.exemplo.com/users/10", {"active": False})
    )

    assert '"active": False,' in generated.content
    assert '"active": "false",' not in generated.content
    ast.parse(generated.content)


def test_patch_nested_structure_is_preserved():
    generated = _generate(
        _json_body_request(
            "PATCH",
            "https://api.exemplo.com/users/10",
            {"address": {"city": "Brasília"}},
        )
    )

    assert (
        "    request_body = {\n"
        '        "address": {\n'
        '            "city": "Brasília",\n'
        "        },\n"
        "    }\n"
    ) in generated.content
    ast.parse(generated.content)


# --- DELETE -------------------------------------------------------------------


def test_delete_without_body_never_gains_an_artificial_body():
    generated = _generate({"request": {"method": "DELETE", "url": "https://api.exemplo.com/users/10"}})

    assert 'response = api_context.delete("/users/10")' in generated.content
    assert generated.content.count("api_context.delete(") == 1
    assert "data=" not in generated.content
    assert "request_body" not in generated.content


def test_delete_with_auth_preserves_auth_without_inventing_a_body():
    generated = _generate(
        {
            "request": {
                "method": "DELETE",
                "url": "https://api.exemplo.com/users/1",
                "auth": {
                    "type": "bearer",
                    "bearer": [{"key": "token", "value": "{{accessToken}}"}],
                },
            }
        },
        _env(accessToken=("token-value", False)),
    )

    assert '"Authorization": f"Bearer {token}",' in generated.content
    assert "data=" not in generated.content
    assert "request_body" not in generated.content
    ast.parse(generated.content)


# --- HEAD -----------------------------------------------------------------


def test_head_with_headers_preserves_them():
    generated = _generate(
        {
            "request": {
                "method": "HEAD",
                "url": "https://api.exemplo.com/users",
                "header": [{"key": "Accept", "value": "application/json"}],
            }
        }
    )

    assert (
        "    response = api_context.head(\n"
        '        "/users",\n'
        "        headers={\n"
        '            "Accept": "application/json",\n'
        "        },\n"
        "    )\n"
    ) in generated.content
    ast.parse(generated.content)


def test_head_with_query_preserves_it():
    generated = _generate(
        {
            "request": {
                "method": "HEAD",
                "url": {
                    "raw": "https://api.exemplo.com/users?active=true",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users"],
                    "query": [{"key": "active", "value": "true"}],
                },
            }
        }
    )

    assert (
        "    response = api_context.head(\n"
        '        "/users",\n'
        "        params={\n"
        '            "active": True,\n'
        "        },\n"
        "    )\n"
    ) in generated.content
    ast.parse(generated.content)


# --- OPTIONS --------------------------------------------------------------


def test_options_with_headers_only():
    generated = _generate(
        {
            "request": {
                "method": "OPTIONS",
                "url": "https://api.exemplo.com/users",
                "header": [{"key": "Accept", "value": "application/json"}],
            }
        }
    )

    assert (
        "    response = api_context.fetch(\n"
        '        "/users",\n'
        '        method="OPTIONS",\n'
        "        headers={\n"
        '            "Accept": "application/json",\n'
        "        },\n"
        "    )\n"
    ) in generated.content
    ast.parse(generated.content)


def test_options_with_query_only():
    generated = _generate(
        {
            "request": {
                "method": "OPTIONS",
                "url": {
                    "raw": "https://api.exemplo.com/users?scope=admin",
                    "protocol": "https",
                    "host": ["api", "exemplo", "com"],
                    "path": ["users"],
                    "query": [{"key": "scope", "value": "admin"}],
                },
            }
        }
    )

    assert (
        "    response = api_context.fetch(\n"
        '        "/users",\n'
        '        method="OPTIONS",\n'
        "        params={\n"
        '            "scope": "admin",\n'
        "        },\n"
        "    )\n"
    ) in generated.content
    ast.parse(generated.content)


def test_options_with_explicit_body_uses_the_same_body_renderer():
    # "Não inventar payload": só quando o NormalizedRequest realmente tem um
    # body — mesmo _resolve_body de sempre, nenhum caminho novo para OPTIONS.
    generated = _generate(_json_body_request("OPTIONS", "https://api.exemplo.com/users", {"probe": True}))

    assert '    request_body = {\n        "probe": True,\n    }\n' in generated.content
    assert (
        "    response = api_context.fetch(\n"
        '        "/users",\n'
        '        method="OPTIONS",\n'
        "        data=request_body,\n"
        "    )\n"
    ) in generated.content
    ast.parse(generated.content)


# --- Tipos: preservação completa (regra: nunca converter/perder tipo) -------


def test_all_scalar_and_null_types_are_preserved_in_the_same_object():
    generated = _generate(
        _json_body_request(
            "POST",
            "https://api.exemplo.com/users",
            {
                "name": "Maria",
                "age": 35,
                "score": 9.5,
                "active": True,
                "archived": False,
                "middleName": None,
            },
        )
    )

    assert (
        "    request_body = {\n"
        '        "name": "Maria",\n'
        '        "age": 35,\n'
        '        "score": 9.5,\n'
        '        "active": True,\n'
        '        "archived": False,\n'
        '        "middleName": None,\n'
        "    }\n"
    ) in generated.content
    ast.parse(generated.content)


def test_nested_objects_and_arrays_of_objects_are_preserved():
    generated = _generate(
        _json_body_request(
            "PUT",
            "https://api.exemplo.com/orders/1",
            {"items": [{"sku": "x"}, {"sku": "y"}]},
        )
    )

    assert (
        "    request_body = {\n"
        '        "items": [\n'
        "            {\n"
        '                "sku": "x",\n'
        "            },\n"
        "            {\n"
        '                "sku": "y",\n'
        "            },\n"
        "        ],\n"
        "    }\n"
    ) in generated.content
    ast.parse(generated.content)


# --- Variáveis: resolução recursiva dentro do body --------------------------


def test_body_root_level_variable_resolves_via_environment():
    generated = _generate(
        _json_body_request("POST", "https://api.exemplo.com/orders", {"region": "{{region}}"}),
        _env(region=("us-east-1", False)),
    )

    assert '"region": "us-east-1",' in generated.content
    assert "os.environ" not in generated.content
    ast.parse(generated.content)


def test_body_nested_object_variable_resolves_recursively():
    generated = _generate(
        _json_body_request(
            "PUT", "https://api.exemplo.com/orders/1", {"customer": {"id": "{{customerId}}"}}
        ),
        _env(customerId=("cust-123", False)),
    )

    assert '"id": "cust-123",' in generated.content
    assert "os.environ" not in generated.content
    ast.parse(generated.content)


def test_body_array_item_variable_resolves_recursively():
    generated = _generate(
        _json_body_request("POST", "https://api.exemplo.com/orders", {"tags": ["{{tag1}}", "fixed"]}),
        _env(tag1=("urgent", False)),
    )

    assert '    request_body = {\n        "tags": [\n            "urgent",\n            "fixed",\n        ],\n    }\n' in (
        generated.content
    )
    ast.parse(generated.content)


# --- Segurança: secret no body nunca vira literal ----------------------------


def test_body_secret_variable_never_appears_as_a_literal():
    generated = _generate(
        _json_body_request("POST", "https://api.exemplo.com/login", {"password": "{{password}}"}),
        _env(password=("super-secret-pw", True)),
    )

    assert "super-secret-pw" not in generated.content
    assert 'password = os.environ.get("AQO_PASSWORD")' in generated.content
    assert '"password": password,' in generated.content
    ast.parse(generated.content)


# --- Body inválido: nunca corrigido por inferência, sempre com warning -----


def test_body_with_non_finite_number_falls_back_with_request_body_not_rendered():
    # json.loads aceita NaN/Infinity (extensão não-padrão), mas repr(float
    # ("nan")) produz o texto "nan" — um NOME indefinido em Python, não um
    # literal válido; nunca corrigido por inferência (arredondar, remover o
    # campo) — o endpoint inteiro cai no fallback, nunca um body
    # aparentemente válido que quebraria a suíte ao rodar de verdade.
    generated = _generate(
        {
            "request": {
                "method": "POST",
                "url": "https://api.exemplo.com/measurements",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": {"mode": "raw", "raw": '{"temperature": NaN}'},
            }
        }
    )

    assert "@pytest.mark.skip" in generated.content
    assert "response = api_context" not in generated.content
    assert len(generated.warnings) == 1
    assert generated.warnings[0].code == REQUEST_BODY_NOT_RENDERED
    assert "nan" not in generated.content.lower()
    ast.parse(generated.content)
