"""Parte 06 do plano de ação Playwright: PlaceholderEndpointTestGenerator —
implementação mínima do contrato EndpointTestGenerator (Parte 03), sem
asserções reais, só para provar a estrutura/persistência da suíte.
"""

import ast
import json

from api_quality_agent.domain.models import TestStrategy
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import PlaceholderEndpointTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser


def _normalized_request_for(method: str, url: str):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Collection",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": [
                    {"name": "R1", "id": "r1", "request": {"method": method, "url": url}}
                ],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)
    return analyzed[0].normalized_request


def test_generated_content_is_syntactically_valid_python():
    strategy = TestStrategy(
        endpoint_source="GET /pets/{id}",
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    request = _normalized_request_for("GET", "https://api.exemplo.com/pets/1")

    generated = PlaceholderEndpointTestGenerator().generate_endpoint(strategy, request)

    ast.parse(generated.content)  # não deve levantar SyntaxError


def test_suggested_file_name_reuses_the_deterministic_naming_from_part_05():
    strategy = TestStrategy(
        endpoint_source="GET /users/{id}",
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    request = _normalized_request_for("GET", "https://api.exemplo.com/users/1")

    generated = PlaceholderEndpointTestGenerator().generate_endpoint(strategy, request)

    assert generated.suggested_file_name == "test_get_users_by_id.py"


def test_endpoint_source_is_preserved_and_no_scenarios_or_warnings_yet():
    strategy = TestStrategy(
        endpoint_source="POST /orders",
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    request = _normalized_request_for("POST", "https://api.exemplo.com/orders")

    generated = PlaceholderEndpointTestGenerator().generate_endpoint(strategy, request)

    assert generated.endpoint_source == "POST /orders"
    assert generated.scenario_names == ()
    assert generated.warnings == ()


def test_content_never_leaks_url_query_string_or_headers():
    # Contrato de segurança já testado para o gerador Postman: comentários
    # gerados nunca podem conter dados potencialmente sensíveis do request
    # (query string, headers, body) — só method e endpoint_source, que já
    # são seguros por construção (method+path).
    strategy = TestStrategy(
        endpoint_source="GET /login",
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    request = _normalized_request_for(
        "GET", "https://api.exemplo.com/login?api_key=super-secreto-123"
    )

    generated = PlaceholderEndpointTestGenerator().generate_endpoint(strategy, request)

    assert "super-secreto-123" not in generated.content
    assert "api_key" not in generated.content
