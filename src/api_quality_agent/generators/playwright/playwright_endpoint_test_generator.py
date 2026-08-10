import json
import re
from dataclasses import replace

from api_quality_agent.domain.models import (
    AuthType,
    NormalizedQueryParameter,
    NormalizedRequest,
    NormalizedUrl,
    PostmanEnvironment,
    TestStrategy,
)
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
# Conversão conservadora de string -> tipo Python: só quando o round-trip
# str(int(value)) == value (exclui "007", "-0" etc., onde a representação
# original pode ser significativa, ex.: CEP/código) — nunca inventa um
# valor, só troca a representação do mesmo dado já presente no request.
_INTEGER_PATTERN = re.compile(r"^-?\d+$")
_BOOLEAN_LITERALS = {"true": True, "false": False}

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
    return _unsupported_query_reason(request.url.query_parameters)


def _has_unresolved_variables(url: NormalizedUrl) -> bool:
    if url.variables:
        return True
    if url.raw and "{{" in url.raw:
        return True
    return any(is_parameterized_segment(segment) for segment in url.path)


def _unsupported_query_reason(
    query_parameters: tuple[NormalizedQueryParameter, ...],
) -> str | None:
    enabled = [q for q in query_parameters if not q.disabled and q.key]

    seen_keys: set[str] = set()
    for parameter in enabled:
        assert parameter.key is not None  # filtrado acima
        if parameter.key in seen_keys:
            # params do Playwright é um dict simples (uma chave -> um
            # valor) — não representa parâmetros repetidos sem perder um
            # deles. "quando representáveis": este caso não é.
            return "parâmetros de query repetidos ainda não são suportados"
        seen_keys.add(parameter.key)

    for parameter in enabled:
        assert parameter.key is not None  # filtrado acima
        if "{{" in parameter.key or "{{" in (parameter.value or ""):
            return "variáveis não resolvidas em query parameters ainda não são suportadas"

    return None


def _relative_path(url: NormalizedUrl) -> str:
    if url.path:
        return "/" + "/".join(url.path)
    return "/"


def _build_query_params(
    query_parameters: tuple[NormalizedQueryParameter, ...],
) -> dict[str, str | int | bool]:
    # Só parâmetros habilitados (nunca gerados os desabilitados); ordem
    # preservada (mesma ordem da Collection, tuple já é determinística);
    # valor ausente/None tratado como string vazia — presente, mas vazio,
    # nunca omitido (distinto de "parâmetro ausente", que nunca chega aqui).
    params: dict[str, str | int | bool] = {}
    for parameter in query_parameters:
        if parameter.disabled or not parameter.key:
            continue
        params[parameter.key] = _coerce_query_value(parameter.value or "")
    return params


def _coerce_query_value(value: str) -> str | int | bool:
    if value in _BOOLEAN_LITERALS:
        return _BOOLEAN_LITERALS[value]
    if _INTEGER_PATTERN.match(value) and str(int(value)) == value:
        return int(value)
    return value


def _render_get_call(path: str, params: dict[str, str | int | bool]) -> str:
    if not params:
        return f"    response = api_context.get({_python_string_literal(path)})\n"

    lines = [
        "    response = api_context.get(\n",
        f"        {_python_string_literal(path)},\n",
        "        params={\n",
    ]
    for key, value in params.items():
        lines.append(
            f"            {_python_string_literal(key)}: {_render_python_literal(value)},\n"
        )
    lines.append("        },\n")
    lines.append("    )\n")
    return "".join(lines)


def _render_python_literal(value: str | int | bool) -> str:
    # bool antes de int: bool é subclasse de int em Python, isinstance(True,
    # int) também é True — checar bool primeiro evita renderizar True/False
    # como 1/0.
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    return _python_string_literal(value)


def _python_string_literal(value: str) -> str:
    # json.dumps produz um literal de string válido em Python (aspas duplas,
    # escapes compatíveis) — ensure_ascii=False preserva acentuação/unicode
    # como texto legível no código gerado, em vez de \\uXXXX, mesma
    # convenção já usada no restante do projeto (ex.: manifesto JSON).
    return json.dumps(value, ensure_ascii=False)


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
        self,
        strategy: TestStrategy,
        request: NormalizedRequest,
        environment: PostmanEnvironment | None = None,
    ) -> GeneratedEndpointTest:
        reason = _unsupported_reason(request)
        if reason is None:
            return _generate_positive_success_test(strategy, request)

        fallback = self._fallback_generator.generate_endpoint(strategy, request, environment)
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
    params = _build_query_params(request.url.query_parameters)

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
        f"{_render_get_call(path, params)}"
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
