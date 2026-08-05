from dataclasses import dataclass

from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)


@dataclass(frozen=True)
class GeneratedEndpointTest:
    # Saída de EndpointTestGenerator.generate_endpoint(...) para um único
    # endpoint — nunca grava nada em disco (ver responsabilidades do
    # contrato em endpoint_test_generator.py). `content` é o código Python
    # do arquivo de teste daquele endpoint; ainda vazio/sem sentido nesta
    # etapa, já que nenhum EndpointTestGenerator concreto existe ainda
    # (plano de ação Playwright, Parte 03 — só contratos e modelos).
    endpoint_source: str
    suggested_file_name: str
    content: str
    scenario_names: tuple[str, ...]
    warnings: tuple[PlaywrightGenerationWarning, ...]
