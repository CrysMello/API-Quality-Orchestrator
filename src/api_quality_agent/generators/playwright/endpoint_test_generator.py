from typing import Protocol

from api_quality_agent.domain.models import NormalizedRequest, TestStrategy
from api_quality_agent.generators.playwright.generated_endpoint_test import GeneratedEndpointTest


class EndpointTestGenerator(Protocol):
    # Responsabilidades (plano de ação Playwright, seção 5.2): recebe o
    # request normalizado e a estratégia de testes de UM endpoint; cria os
    # cenários e o conteúdo Python correspondentes; registra warnings;
    # informa o nome sugerido do arquivo (via GeneratedEndpointTest). Nunca
    # grava nada em disco — quem persiste é a borda, não este contrato.
    def generate_endpoint(
        self, strategy: TestStrategy, request: NormalizedRequest
    ) -> GeneratedEndpointTest: ...
