from collections.abc import Sequence
from typing import Protocol

from api_quality_agent.domain.models import ExecutionContext
from api_quality_agent.generators.playwright.generated_endpoint_test import GeneratedEndpointTest
from api_quality_agent.generators.playwright.generated_test_suite import GeneratedTestSuite


class PlaywrightTestSuiteBuilder(Protocol):
    # Responsabilidades (plano de ação Playwright, seção 5.2): reúne os
    # GeneratedEndpointTest já produzidos por endpoint; resolve colisões de
    # nome de arquivo; organiza a suíte (ex.: conftest.py); nunca recria
    # estratégias ou cria expectativas novas — isso é papel exclusivo do
    # EndpointTestGenerator, que roda antes dele no pipeline.
    #
    # `context` usa o ExecutionContext já existente no domínio (execution_id,
    # workspace/collection, modo online/offline) em vez de um tipo novo — o
    # plano de ação original menciona um "CollectionGenerationContext"
    # conceitual, mas ExecutionContext já cobre exatamente essa informação
    # sem duplicar modelo.
    def build(
        self,
        endpoint_tests: Sequence[GeneratedEndpointTest],
        context: ExecutionContext,
    ) -> GeneratedTestSuite: ...
