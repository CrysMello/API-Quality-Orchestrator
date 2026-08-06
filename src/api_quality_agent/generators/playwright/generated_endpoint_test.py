from dataclasses import dataclass

from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)


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
