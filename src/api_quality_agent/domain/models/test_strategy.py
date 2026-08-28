from dataclasses import dataclass

from api_quality_agent.domain.models.assertion_definition import AssertionDefinition
from api_quality_agent.domain.models.negative_case_definition import NegativeCaseDefinition
from api_quality_agent.domain.models.strategy_warning import StrategyWarning
from api_quality_agent.domain.models.variable_extraction import VariableExtraction
from api_quality_agent.domain.models.variable_usage import VariableUsage


@dataclass(frozen=True)
class TestStrategy:
    endpoint_source: str
    assertions: tuple[AssertionDefinition, ...]
    variable_extractions: tuple[VariableExtraction, ...]
    negative_cases: tuple[NegativeCaseDefinition, ...]
    warnings: tuple[StrategyWarning, ...]
    # Dependências entre endpoints (aditivo — default preserva toda
    # construção existente, inclusive o fluxo Postman, que nunca preenche
    # isto): uma entrada por variável que ESTE endpoint consome, produzida
    # por outro endpoint em runtime — nunca resolvido por {{variável}} do
    # Postman nem por um literal conhecido na geração. Populado por uma
    # etapa de linkagem externa (ver endpoint_dependency_linking.py),
    # nunca pelo TestStrategyEngine (que só enxerga um endpoint por vez) e
    # nunca pelo PlaywrightEndpointTestGenerator (que nunca descobre
    # outros endpoints sozinho).
    variable_usages: tuple[VariableUsage, ...] = ()
