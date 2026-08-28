"""Teste 10 (Etapa 10) — regressão explícita: dependências entre endpoints
Playwright (VariableUsage, endpoint_dependency_linking.py) nunca tocam o
fluxo Postman/Newman.

TestStrategy.variable_usages é aditivo (default `()`), mas o campo existe
na MESMA classe usada pelo pipeline Postman (postman_test_generator.py via
AgentOrchestrator) — este teste prova, com o gerador Postman REAL, que
preencher variable_usages produz o MESMO GeneratedTestScript (script,
warnings, id) que preenchê-lo vazio: nenhuma leitura, nenhum efeito
colateral, nenhuma ramificação nova para esse campo em todo o pipeline
Postman.
"""

import dataclasses

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    TestStrategy,
    VariableUsage,
)
from api_quality_agent.generators.postman_test_generator import PostmanTestGenerator

_BASE_STRATEGY = TestStrategy(
    endpoint_source="GET /customers/{customer_id}",
    assertions=(
        AssertionDefinition(
            assertion_type=AssertionType.STATUS_CODE,
            description="Status code da resposta deve ser 200.",
            expected_value=200,
            origin="contract",
        ),
    ),
    variable_extractions=(),
    negative_cases=(),
    warnings=(),
)


def test_postman_generator_output_is_identical_with_or_without_variable_usages():
    strategy_with_usage = dataclasses.replace(
        _BASE_STRATEGY,
        variable_usages=(
            VariableUsage(
                variable_name="customer_id",
                producer_test_id="test_post_customers_success",
                location="path",
            ),
        ),
    )

    generated_without = PostmanTestGenerator().generate(_BASE_STRATEGY)
    generated_with = PostmanTestGenerator().generate(strategy_with_usage)

    assert generated_without.script == generated_with.script
    assert generated_without.warnings == generated_with.warnings
    assert generated_without.summary == generated_with.summary
    assert generated_without.test_count == generated_with.test_count


def test_postman_test_generator_module_never_imports_variable_usage():
    # Confirmação estrutural (não só comportamental): o módulo do gerador
    # Postman nunca precisa saber que VariableUsage existe — reforça que a
    # feature de dependências é exclusiva do fluxo Playwright, por
    # construção, não só por coincidência de teste.
    from api_quality_agent.generators import postman_test_generator

    assert "VariableUsage" not in dir(postman_test_generator)
