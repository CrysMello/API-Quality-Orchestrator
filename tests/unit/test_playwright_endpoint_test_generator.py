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


def _generate(request: dict):
    strategy_source, normalized_request = _analyzed(request)
    from api_quality_agent.domain.models import TestStrategy

    strategy = TestStrategy(
        endpoint_source=strategy_source.source,
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)


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


def test_content_never_leaks_query_string_secrets():
    generated = _generate(
        {
            "request": {
                "method": "GET",
                "url": "https://api.exemplo.com/login?api_key=super-secreto-123",
            }
        }
    )

    assert "super-secreto-123" not in generated.content
    assert "api_key" not in generated.content


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
