import json
from dataclasses import replace

from api_quality_agent.domain.models import AuthType, NormalizedRequest, NormalizedUrl, TestStrategy
from api_quality_agent.generators.playwright.base_url import derive_base_url
from api_quality_agent.generators.playwright.endpoint_file_naming import (
    endpoint_source_to_file_name,
    endpoint_source_to_slug,
    is_parameterized_segment,
)
from api_quality_agent.generators.playwright.endpoint_test_generator import EndpointTestGenerator
from api_quality_agent.generators.playwright.generated_endpoint_test import GeneratedEndpointTest
from api_quality_agent.generators.playwright.placeholder_endpoint_test_generator import (
    PlaceholderEndpointTestGenerator,
)
from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)

_NO_AUTH_TYPES = (AuthType.NONE, AuthType.INHERIT, AuthType.UNKNOWN)

ENDPOINT_NOT_SUPPORTED_YET = "ENDPOINT_NOT_SUPPORTED_YET"


def _single_line(text: str) -> str:
    # Nunca deve poder fechar a docstring triple-quoted onde é embutido nem
    # introduzir uma quebra de linha inesperada — endpoint_source/request.name
    # vêm do documento de origem (Collection/OpenAPI), não são controlados
    # por este código.
    return text.replace("\n", " ").replace("\r", " ").replace('"""', "'''")


def _unsupported_reason(request: NormalizedRequest) -> str | None:
    # Caso mais simples primeiro (Parte 07): GET, sem body, sem
    # autenticação, sem variáveis de path não resolvidas. Qualquer coisa
    # além disso ainda cai no fallback (placeholder + warning) — nunca um
    # código enganoso que pareça testar algo que não testa de verdade.
    method = (request.method or "").upper()
    if method != "GET":
        return f"método {request.method or 'desconhecido'} ainda não suportado"
    if request.body.has_content:
        return "requests com body ainda não são suportadas"
    if request.auth.auth_type not in _NO_AUTH_TYPES:
        return "autenticação ainda não é suportada"
    if _has_unresolved_variables(request.url):
        return "variáveis não resolvidas na URL ainda não são suportadas"
    return None


def _has_unresolved_variables(url: NormalizedUrl) -> bool:
    if url.variables:
        return True
    if url.raw and "{{" in url.raw:
        return True
    return any(is_parameterized_segment(segment) for segment in url.path)


def _relative_path(url: NormalizedUrl) -> str:
    if url.path:
        return "/" + "/".join(url.path)
    return "/"


class PlaywrightEndpointTestGenerator:
    # Implementação real do contrato EndpointTestGenerator (Parte 03),
    # substituindo o PlaceholderEndpointTestGenerator como gerador padrão
    # (Parte 07). Cobre só o caso mais simples — GET, sem body, sem
    # autenticação, sem variáveis não resolvidas — com um cenário positivo
    # básico; qualquer endpoint fora disso cai no fallback (mesmo conteúdo
    # do PlaceholderEndpointTestGenerator, com um warning explicando por
    # quê), nunca em código que finja testar algo que não testa.
    def __init__(self, fallback_generator: EndpointTestGenerator | None = None) -> None:
        self._fallback_generator = fallback_generator or PlaceholderEndpointTestGenerator()

    def generate_endpoint(
        self, strategy: TestStrategy, request: NormalizedRequest
    ) -> GeneratedEndpointTest:
        reason = _unsupported_reason(request)
        if reason is None:
            return _generate_positive_success_test(strategy, request)

        fallback = self._fallback_generator.generate_endpoint(strategy, request)
        warning = PlaywrightGenerationWarning(
            code=ENDPOINT_NOT_SUPPORTED_YET,
            message=f"Geração real ainda não suportada para este endpoint: {reason}.",
            endpoint=strategy.endpoint_source,
            scenario=None,
        )
        return replace(fallback, warnings=fallback.warnings + (warning,))


def _generate_positive_success_test(
    strategy: TestStrategy, request: NormalizedRequest
) -> GeneratedEndpointTest:
    slug = endpoint_source_to_slug(strategy.endpoint_source)
    function_name = f"test_{slug}_success"
    path = _relative_path(request.url)

    safe_request_name = _single_line(request.name or strategy.endpoint_source)
    safe_endpoint_source = _single_line(strategy.endpoint_source)

    content = (
        f"def {function_name}(api_context):\n"
        '    """\n'
        f"    Request: {safe_request_name}\n"
        f"    Method: {request.method}\n"
        f"    Endpoint: {safe_endpoint_source}\n"
        "    Scenario: success\n"
        "    Category: positive\n"
        "    Origin: NormalizedRequest (GET simples, sem body, sem "
        "autenticação, sem variáveis não resolvidas)\n"
        '    """\n'
        "\n"
        f"    response = api_context.get({json.dumps(path)})\n"
        "\n"
        "    assert response is not None\n"
    )

    return GeneratedEndpointTest(
        endpoint_source=strategy.endpoint_source,
        suggested_file_name=endpoint_source_to_file_name(strategy.endpoint_source),
        content=content,
        scenario_names=("success",),
        warnings=(),
        base_url=derive_base_url(request.url),
    )
