from api_quality_agent.domain.models import NormalizedRequest, TestStrategy
from api_quality_agent.generators.playwright.endpoint_file_naming import (
    endpoint_source_to_file_name,
)
from api_quality_agent.generators.playwright.generated_endpoint_test import GeneratedEndpointTest


class PlaceholderEndpointTestGenerator:
    # Implementação mínima do contrato EndpointTestGenerator (Parte 03):
    # produz um arquivo Python sintaticamente válido para cada endpoint,
    # mas sem nenhuma asserção real ainda — existe só para provar a
    # estrutura física e a persistência da suíte (Parte 06). O conteúdo
    # real (asserções por AssertionType, cenários negativos, extração de
    # variáveis) é escopo de uma etapa futura, deliberadamente fora daqui.
    def generate_endpoint(
        self, strategy: TestStrategy, request: NormalizedRequest
    ) -> GeneratedEndpointTest:
        return GeneratedEndpointTest(
            endpoint_source=strategy.endpoint_source,
            suggested_file_name=endpoint_source_to_file_name(strategy.endpoint_source),
            content=_render_placeholder_content(strategy, request),
            scenario_names=(),
            warnings=(),
        )


def _render_placeholder_content(strategy: TestStrategy, request: NormalizedRequest) -> str:
    # strategy.endpoint_source já é seguro para aparecer em comentários (o
    # mesmo texto usado em warnings/summary do gerador Postman) — mas nunca
    # deve conter quebra de linha embutida, então é normalizado por
    # segurança antes de virar comentário de uma única linha.
    safe_endpoint_source = strategy.endpoint_source.replace("\n", " ").replace("\r", " ")
    method = (request.method or "?").replace("\n", " ").replace("\r", " ")

    return (
        '"""Conteúdo completo (asserções, cenários negativos, extração de '
        'variáveis) ainda não implementado — ver plano de ação Playwright."""\n'
        "\n"
        "import pytest\n"
        "\n"
        "\n"
        f"# Endpoint: {safe_endpoint_source}\n"
        f"# Método: {method}\n"
        '@pytest.mark.skip(reason="Geração de asserções Playwright ainda não implementada.")\n'
        "def test_placeholder() -> None:\n"
        "    ...\n"
    )
