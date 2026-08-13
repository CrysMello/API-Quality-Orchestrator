from typing import Protocol

from api_quality_agent.domain.models import NormalizedRequest, PostmanEnvironment, TestStrategy
from api_quality_agent.generators.playwright.generated_endpoint_test import GeneratedEndpointTest


class EndpointTestGenerator(Protocol):
    # Responsabilidades (plano de ação Playwright, seção 5.2): recebe o
    # request normalizado e a estratégia de testes de UM endpoint; cria os
    # cenários e o conteúdo Python correspondentes; registra warnings;
    # informa o nome sugerido do arquivo (via GeneratedEndpointTest). Nunca
    # grava nada em disco — quem persiste é a borda, não este contrato.
    #
    # `environment` (Parte 09) disponibiliza ao gerador as variáveis de um
    # Environment do Postman opcional — default None preserva qualquer
    # chamada existente. Nenhum valor (muito menos um secreto) deve ser
    # embutido literalmente no código gerado a partir dele; essa é uma
    # decisão de uma etapa futura, não desta.
    def generate_endpoint(
        self,
        strategy: TestStrategy,
        request: NormalizedRequest,
        environment: PostmanEnvironment | None = None,
    ) -> GeneratedEndpointTest: ...
