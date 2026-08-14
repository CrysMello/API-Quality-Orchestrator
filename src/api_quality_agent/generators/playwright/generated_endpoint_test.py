from dataclasses import dataclass

from api_quality_agent.generators.playwright.assertion_classification import (
    AssertionClassification,
)
from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)
from api_quality_agent.generators.playwright.variable_resolver import UnresolvedVariable


@dataclass(frozen=True)
class GeneratedEndpointTest:
    # Saída de EndpointTestGenerator.generate_endpoint(...) para um único
    # endpoint — nunca grava nada em disco (ver responsabilidades do
    # contrato em endpoint_test_generator.py). `content` é o código Python
    # do arquivo de teste daquele endpoint.
    endpoint_source: str
    suggested_file_name: str
    content: str
    scenario_names: tuple[str, ...]
    warnings: tuple[PlaywrightGenerationWarning, ...]
    # scheme+host derivado do NormalizedRequest (ver base_url.py), quando
    # determinável — usado pela suíte (conftest.py, Parte 08) para
    # configurar o base_url padrão do APIRequestContext. None quando não
    # há evidência suficiente (nunca inventado).
    base_url: str | None = None
    # Rastreabilidade de variáveis (Parte 15), consumida por
    # DefaultPlaywrightTestSuiteBuilder para montar o generation-manifest.json
    # — nunca preenchida pelo PlaceholderEndpointTestGenerator (endpoint que
    # cai no fallback não resolve nada de verdade, só registra o que ficou
    # sem resolução via unresolved_variables).
    #
    # AQO_* referenciadas pelo código deste endpoint — tanto por variável
    # deferida quanto por secret (nunca o valor do secret, só o nome).
    required_environment_variables: tuple[str, ...] = ()
    # nome Postman -> valor literal resolvido — nunca um secret (ver
    # VariableResolutionSession.resolved_variables).
    resolved_variables: tuple[tuple[str, str], ...] = ()
    # Variáveis que não puderam ser resolvidas (sem Environment, sem valor
    # literal na Collection, sem forma segura de deferir) — location indica
    # o campo onde apareceram (path, base_url, query, header, auth, body,
    # multipart_field).
    unresolved_variables: tuple[UnresolvedVariable, ...] = ()
    # Nível de confiança (EXACT/DERIVED/BROAD) de cada expectativa
    # realmente gerada no cenário "success" (Parte 23) — uma entrada por
    # categoria de asserção (status/content_type/body/required_fields/
    # field_types/json_schema/expected_values) que de fato produziu algum
    # código; nunca uma entrada para uma categoria sem evidência nenhuma
    # (nada gerado, nada a classificar). Consumida por
    # DefaultPlaywrightTestSuiteBuilder para o generation-manifest.json.
    assertion_classifications: tuple[AssertionClassification, ...] = ()
