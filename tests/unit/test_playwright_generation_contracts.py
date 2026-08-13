"""Parte 03 do plano de ação Playwright: modelos e contratos da geração,
sem nenhuma renderização/persistência real ainda.

Cobre: imutabilidade dos modelos, warnings vinculados a endpoint+cenário, e
compatibilidade estrutural de implementações fake com os dois Protocols
(EndpointTestGenerator, PlaywrightTestSuiteBuilder) — a garantia de tipagem
completa em si é responsabilidade do mypy (rodado à parte), não deste teste.
"""

import dataclasses
import json

import pytest

from api_quality_agent.domain.models import ExecutionContext, ExecutionMode
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    AssertionPrecision,
    EndpointTestGenerator,
    GeneratedEndpointTest,
    GeneratedFile,
    GeneratedTestSuite,
    PlaywrightGenerationWarning,
    PlaywrightTestSuiteBuilder,
)
from api_quality_agent.parsers import PostmanCollectionParser


def _normalized_request_for_get_pets():
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
                    {
                        "name": "Listar pets",
                        "id": "r1",
                        "request": {"method": "GET", "url": "https://api.exemplo.com/pets"},
                    }
                ],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)
    return analyzed[0].analysis, analyzed[0].normalized_request


# --- AssertionPrecision ------------------------------------------------------


def test_assertion_precision_has_the_three_expected_levels():
    assert {member.value for member in AssertionPrecision} == {"exact", "derived", "broad"}


# --- Imutabilidade dos modelos -----------------------------------------------


def test_playwright_generation_warning_is_frozen():
    warning = PlaywrightGenerationWarning(
        code="BROAD_STATUS_ASSERTION",
        message="Sem status exato para este cenário.",
        endpoint="POST /users",
        scenario="missing_required_name",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        warning.code = "OUTRO"  # type: ignore[misc]


def test_generated_endpoint_test_is_frozen_and_keeps_warnings_linked_to_scenario():
    warning = PlaywrightGenerationWarning(
        code="BROAD_STATUS_ASSERTION",
        message="Sem status exato para este cenário.",
        endpoint="POST /users",
        scenario="missing_required_name",
    )
    endpoint_test = GeneratedEndpointTest(
        endpoint_source="POST /users",
        suggested_file_name="test_post_users.py",
        content="",
        scenario_names=("missing_required_name",),
        warnings=(warning,),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        endpoint_test.content = "outra coisa"  # type: ignore[misc]

    assert endpoint_test.warnings[0].scenario in endpoint_test.scenario_names
    assert endpoint_test.warnings[0].endpoint == endpoint_test.endpoint_source


def test_generated_file_is_frozen():
    generated_file = GeneratedFile(relative_path="endpoints/test_post_users.py", content="")

    with pytest.raises(dataclasses.FrozenInstanceError):
        generated_file.content = "outra coisa"  # type: ignore[misc]


def test_generated_test_suite_is_frozen_and_aggregates_files_and_warnings():
    warning = PlaywrightGenerationWarning(
        code="FILE_NAME_COLLISION_RESOLVED",
        message="Nome duplicado, sufixo aplicado.",
        endpoint="GET /users",
        scenario=None,
    )
    suite = GeneratedTestSuite(
        files=(GeneratedFile(relative_path="endpoints/test_get_users.py", content=""),),
        warnings=(warning,),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        suite.files = ()  # type: ignore[misc]

    assert len(suite.files) == 1
    assert suite.warnings[0].code == "FILE_NAME_COLLISION_RESOLVED"


# --- Compatibilidade estrutural com os contratos -----------------------------


class _FakeEndpointTestGenerator:
    # Implementação mínima só para comprovar compatibilidade estrutural com
    # o Protocol EndpointTestGenerator — nenhuma lógica de geração real
    # ainda (fora de escopo da Parte 03).
    def generate_endpoint(self, strategy, request) -> GeneratedEndpointTest:
        return GeneratedEndpointTest(
            endpoint_source=strategy.endpoint_source,
            suggested_file_name="test_fake.py",
            content="",
            scenario_names=(),
            warnings=(),
        )


class _FakeTestSuiteBuilder:
    def build(self, endpoint_tests, context: ExecutionContext) -> GeneratedTestSuite:
        return GeneratedTestSuite(files=(), warnings=())


def test_fake_endpoint_test_generator_satisfies_the_contract():
    from api_quality_agent.domain.models import TestStrategy

    generator: EndpointTestGenerator = _FakeEndpointTestGenerator()
    _analysis, normalized_request = _normalized_request_for_get_pets()
    strategy = TestStrategy(
        endpoint_source="GET /pets",
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    result = generator.generate_endpoint(strategy, normalized_request)

    assert isinstance(result, GeneratedEndpointTest)
    assert result.endpoint_source == "GET /pets"


def test_fake_suite_builder_satisfies_the_contract():
    builder: PlaywrightTestSuiteBuilder = _FakeTestSuiteBuilder()
    context = ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="local-file")

    result = builder.build([], context)

    assert isinstance(result, GeneratedTestSuite)
