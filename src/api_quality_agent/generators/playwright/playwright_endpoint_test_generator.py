import json
import re
from dataclasses import dataclass, replace

from api_quality_agent.domain.models import (
    AuthType,
    NormalizedAuth,
    NormalizedAuthParameter,
    NormalizedHeader,
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
    to_snake_case,
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

# Nomes reservados (case-insensitive): nunca renderizados como header
# genérico, mesmo quando presentes e habilitados no NormalizedRequest.
# - authorization: sempre tratado como sensível — quando vem de
#   autenticação estruturada suportada (Parte 12), é a própria
#   _resolve_auth que escreve esse header, nunca um valor manual.
# - content-type: reservado para uma geração futura derivada do tipo de
#   body ("Content-Type específico por tipo de body" — não implementado
#   aqui) — evita duas fontes divergentes escrevendo o mesmo header.
_RESERVED_HEADER_NAMES = frozenset({"authorization", "content-type"})

HEADER_VALUE_NOT_RESOLVED = "HEADER_VALUE_NOT_RESOLVED"
SENSITIVE_HEADER_OMITTED = "SENSITIVE_HEADER_OMITTED"
RESERVED_HEADER_OMITTED = "RESERVED_HEADER_OMITTED"
DUPLICATE_HEADER_IGNORED = "DUPLICATE_HEADER_IGNORED"

# Parte 12 — autenticação suportada: Bearer Token, API Key (header ou
# query) e Basic Auth, só quando o(s) valor(es) relevante(s) forem uma
# referência pura a uma variável Postman ({{nome}}, nada mais na string) —
# nunca um segredo literal embutido na Collection. O nome da variável vira
# o nome da variável de ambiente lida em tempo de execução, nunca o valor
# em si (ver _to_env_var_name).
AUTHENTICATION_NOT_SUPPORTED = "AUTHENTICATION_NOT_SUPPORTED"
AUTHENTICATION_VALUE_NOT_RESOLVED = "AUTHENTICATION_VALUE_NOT_RESOLVED"
_PURE_VARIABLE_REFERENCE = re.compile(r"^\{\{\s*([^{}]+?)\s*\}\}$")
_ENV_VAR_PREFIX = "AQO_"


def _single_line(text: str) -> str:
    # Nunca deve poder fechar a docstring triple-quoted onde é embutido nem
    # introduzir uma quebra de linha inesperada — endpoint_source/request.name
    # vêm do documento de origem (Collection/OpenAPI), não são controlados
    # por este código.
    return text.replace("\n", " ").replace("\r", " ").replace('"""', "'''")


@dataclass(frozen=True)
class _UnsupportedReason:
    code: str
    message: str


def _unsupported_reason(request: NormalizedRequest) -> _UnsupportedReason | None:
    # Caso mais simples primeiro (Parte 07): GET, sem body, sem variáveis
    # de path não resolvidas, com autenticação suportada (Parte 12) ou
    # nenhuma. Qualquer coisa além disso ainda cai no fallback (placeholder
    # + warning) — nunca um código enganoso que pareça testar algo que não
    # testa de verdade.
    method = (request.method or "").upper()
    if method != "GET":
        return _UnsupportedReason(
            ENDPOINT_NOT_SUPPORTED_YET,
            f"método {request.method or 'desconhecido'} ainda não suportado",
        )
    if request.body.has_content:
        return _UnsupportedReason(
            ENDPOINT_NOT_SUPPORTED_YET, "requests com body ainda não são suportadas"
        )

    auth_resolution = _resolve_auth(request.auth)
    if not auth_resolution.supported:
        assert auth_resolution.reason_code is not None  # garantido por _unsupported_auth
        assert auth_resolution.reason_message is not None
        return _UnsupportedReason(auth_resolution.reason_code, auth_resolution.reason_message)

    if _has_unresolved_variables(request.url):
        return _UnsupportedReason(
            ENDPOINT_NOT_SUPPORTED_YET, "variáveis não resolvidas na URL ainda não são suportadas"
        )

    query_reason = _unsupported_query_reason(request.url.query_parameters)
    if query_reason is not None:
        return _UnsupportedReason(ENDPOINT_NOT_SUPPORTED_YET, query_reason)

    return None


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
) -> dict[str, str]:
    # Só parâmetros habilitados (nunca gerados os desabilitados); ordem
    # preservada (mesma ordem da Collection, tuple já é determinística);
    # valor ausente/None tratado como string vazia — presente, mas vazio,
    # nunca omitido (distinto de "parâmetro ausente", que nunca chega aqui).
    # Valor já vem pré-renderizado como código Python (literal), para poder
    # conviver no mesmo dict com parâmetros vindos de _resolve_auth
    # (Parte 12), que são expressões — não literais — e não passam por
    # _render_python_literal.
    params: dict[str, str] = {}
    for parameter in query_parameters:
        if parameter.disabled or not parameter.key:
            continue
        params[parameter.key] = _render_python_literal(_coerce_query_value(parameter.value or ""))
    return params


def _coerce_query_value(value: str) -> str | int | bool:
    if value in _BOOLEAN_LITERALS:
        return _BOOLEAN_LITERALS[value]
    if _INTEGER_PATTERN.match(value) and str(int(value)) == value:
        return int(value)
    return value


@dataclass(frozen=True)
class _HeaderResolution:
    headers: dict[str, str]
    warnings: tuple[PlaywrightGenerationWarning, ...]


def _resolve_headers(
    headers: tuple[NormalizedHeader, ...],
    *,
    endpoint_source: str,
    environment: PostmanEnvironment | None,
) -> _HeaderResolution:
    # Só headers habilitados entram na consideração — os demais são
    # ignorados sem gerar warning (desabilitado é uma decisão explícita já
    # tomada na Collection, não uma omissão nossa a explicar).
    enabled = [h for h in headers if not h.disabled and h.key]

    resolved: dict[str, tuple[str, str]] = {}  # chave normalizada -> (nome original, valor)
    warnings: list[PlaywrightGenerationWarning] = []

    for header in enabled:
        assert header.key is not None  # filtrado acima
        key = header.key
        lower_key = key.lower()
        value = header.value or ""

        if lower_key in _RESERVED_HEADER_NAMES:
            code = SENSITIVE_HEADER_OMITTED if lower_key == "authorization" else RESERVED_HEADER_OMITTED
            warnings.append(_header_warning(code, endpoint_source, key, _reserved_reason(lower_key)))
            continue

        if "{{" in key or "{{" in value:
            warnings.append(
                _header_warning(
                    HEADER_VALUE_NOT_RESOLVED,
                    endpoint_source,
                    key,
                    "nome ou valor contém uma variável não resolvida",
                )
            )
            continue

        if _matches_known_secret(value, environment):
            warnings.append(
                _header_warning(
                    SENSITIVE_HEADER_OMITTED,
                    endpoint_source,
                    key,
                    "valor corresponde a uma variável marcada como secreta no Environment",
                )
            )
            continue

        if lower_key in resolved:
            # Case-insensitive: "Accept" e "accept" (ou dois "Accept"
            # literais) são o mesmo header HTTP — mantém o último valor
            # definido, avisa sobre o anterior descartado.
            warnings.append(
                _header_warning(
                    DUPLICATE_HEADER_IGNORED,
                    endpoint_source,
                    key,
                    "duplicado (diferença de caixa incluída); mantido o último valor definido",
                )
            )
        resolved[lower_key] = (key, value)

    # Valor pré-renderizado como código Python (literal) — mesmo motivo de
    # _build_query_params: precisa conviver com headers vindos de
    # _resolve_auth (Parte 12), que são expressões, não literais.
    ordered_headers = {
        original_key: _python_string_literal(value) for original_key, value in resolved.values()
    }
    return _HeaderResolution(headers=ordered_headers, warnings=tuple(warnings))


def _reserved_reason(lower_key: str) -> str:
    if lower_key == "authorization":
        return "cabeçalhos de autenticação ainda não são gerados automaticamente"
    return "reservado para uma geração futura derivada do tipo de body"


def _matches_known_secret(value: str, environment: PostmanEnvironment | None) -> bool:
    if not value or environment is None:
        return False
    return any(
        variable.is_secret and variable.enabled and variable.value == value
        for variable in environment.variables
    )


def _header_warning(
    code: str, endpoint_source: str, header_key: str, reason: str
) -> PlaywrightGenerationWarning:
    # Nunca o valor do header — só o nome (já seguro, mesmo padrão de
    # strategy.endpoint_source) — aparece na mensagem.
    safe_key = _single_line(header_key)
    return PlaywrightGenerationWarning(
        code=code,
        message=f"Header '{safe_key}' omitido: {reason}.",
        endpoint=endpoint_source,
        scenario=None,
    )


def _render_get_call(
    path: str,
    params: dict[str, str],
    headers: dict[str, str],
) -> str:
    # params/headers já chegam pré-renderizados como código Python (cada
    # valor é ou um literal escapado — _python_string_literal/
    # _render_python_literal — ou uma expressão de _resolve_auth, ex.:
    # 'f"Bearer {token}"') — este ponto só monta o texto, nunca decide como
    # cada valor deve ser representado.
    if not params and not headers:
        return f"    response = api_context.get({_python_string_literal(path)})\n"

    lines = [
        "    response = api_context.get(\n",
        f"        {_python_string_literal(path)},\n",
    ]
    if params:
        lines.append("        params={\n")
        for key, value in params.items():
            lines.append(f"            {_python_string_literal(key)}: {value},\n")
        lines.append("        },\n")
    if headers:
        # Header específico do endpoint: tem precedência sobre um header
        # de mesmo nome definido em _SHARED_HEADERS (conftest.py) —
        # comportamento nativo do Playwright (headers por requisição
        # sobrescrevem extra_http_headers do contexto), não algo que este
        # código precisa mesclar manualmente.
        lines.append("        headers={\n")
        for key, value in headers.items():
            lines.append(f"            {_python_string_literal(key)}: {value},\n")
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


# --- Autenticação (Parte 12) -------------------------------------------------


@dataclass(frozen=True)
class _AuthResolution:
    supported: bool
    reason_code: str | None
    reason_message: str | None
    # Linhas já formatadas (indentação de 4 espaços + quebra de linha
    # incluídas) para inserir no corpo da função, antes da chamada
    # api_context.get(...) — ex.: leitura da variável de ambiente + assert.
    preamble_lines: tuple[str, ...]
    # Chave -> expressão Python já pronta (não um literal cru) — ex.:
    # 'f"Bearer {token}"' ou "api_key" (nome de variável local definida no
    # preâmbulo). Mesma convenção de valor pré-renderizado usada por
    # _build_query_params/_resolve_headers.
    extra_headers: dict[str, str]
    extra_params: dict[str, str]
    extra_imports: frozenset[str]


def _unsupported_auth(code: str, message: str) -> _AuthResolution:
    return _AuthResolution(
        supported=False,
        reason_code=code,
        reason_message=message,
        preamble_lines=(),
        extra_headers={},
        extra_params={},
        extra_imports=frozenset(),
    )


def _supported_auth(
    *,
    preamble_lines: tuple[str, ...] = (),
    extra_headers: dict[str, str] | None = None,
    extra_params: dict[str, str] | None = None,
    extra_imports: frozenset[str] = frozenset(),
) -> _AuthResolution:
    return _AuthResolution(
        supported=True,
        reason_code=None,
        reason_message=None,
        preamble_lines=preamble_lines,
        extra_headers=extra_headers or {},
        extra_params=extra_params or {},
        extra_imports=extra_imports,
    )


def _find_auth_param(
    parameters: tuple[NormalizedAuthParameter, ...], key: str
) -> NormalizedAuthParameter | None:
    return next((parameter for parameter in parameters if parameter.key == key), None)


def _extract_pure_variable_name(value: str | None) -> str | None:
    # Só uma referência de variável Postman, nada mais na string (ex.:
    # "{{accessToken}}" resolve; "Bearer {{accessToken}}" ou um valor
    # literal não resolvem) — evidência estrutural mínima para nunca tratar
    # um segredo hardcoded na Collection como se fosse seguro de embutir.
    if not value:
        return None
    match = _PURE_VARIABLE_REFERENCE.match(value)
    return match.group(1) if match else None


def _to_env_var_name(variable_name: str) -> str:
    # apiKey -> api_key -> AQO_API_KEY; accessToken -> AQO_ACCESS_TOKEN.
    return f"{_ENV_VAR_PREFIX}{to_snake_case(variable_name).upper()}"


def _env_var_lookup_lines(local_variable: str, env_var: str) -> tuple[str, ...]:
    # "Validação clara de variável obrigatória": o teste falha explicando
    # exatamente qual variável de ambiente configurar, em vez de um erro
    # genérico de autenticação vindo de dentro do Playwright.
    return (
        f'    {local_variable} = os.environ.get("{env_var}")\n',
        f'    assert {local_variable}, '
        f'"Variável de ambiente obrigatória {env_var} não definida."\n',
    )


def _resolve_bearer_auth(auth: NormalizedAuth) -> _AuthResolution:
    token_param = _find_auth_param(auth.parameters, "token")
    if token_param is None or not token_param.value:
        return _unsupported_auth(
            AUTHENTICATION_NOT_SUPPORTED, "Bearer Token sem o parâmetro 'token' definido"
        )

    variable_name = _extract_pure_variable_name(token_param.value)
    if variable_name is None:
        return _unsupported_auth(
            AUTHENTICATION_VALUE_NOT_RESOLVED,
            "valor do Bearer Token não é uma referência de variável ({{...}}) resolvível",
        )

    env_var = _to_env_var_name(variable_name)
    return _supported_auth(
        preamble_lines=_env_var_lookup_lines("token", env_var),
        extra_headers={"Authorization": 'f"Bearer {token}"'},
        extra_imports=frozenset({"os"}),
    )


def _resolve_api_key_auth(auth: NormalizedAuth) -> _AuthResolution:
    key_param = _find_auth_param(auth.parameters, "key")
    value_param = _find_auth_param(auth.parameters, "value")
    if key_param is None or not key_param.value or value_param is None or not value_param.value:
        return _unsupported_auth(
            AUTHENTICATION_NOT_SUPPORTED, "API Key sem 'key' e/ou 'value' definidos"
        )

    variable_name = _extract_pure_variable_name(value_param.value)
    if variable_name is None:
        return _unsupported_auth(
            AUTHENTICATION_VALUE_NOT_RESOLVED,
            "valor da API Key não é uma referência de variável ({{...}}) resolvível",
        )

    location_param = _find_auth_param(auth.parameters, "in")
    location = ((location_param.value if location_param else None) or "header").lower()
    if location not in ("header", "query"):
        return _unsupported_auth(
            AUTHENTICATION_NOT_SUPPORTED, f"localização de API Key '{location}' não suportada"
        )

    env_var = _to_env_var_name(variable_name)
    preamble = _env_var_lookup_lines("api_key", env_var)
    param_name = key_param.value

    if location == "header":
        return _supported_auth(
            preamble_lines=preamble,
            extra_headers={param_name: "api_key"},
            extra_imports=frozenset({"os"}),
        )
    return _supported_auth(
        preamble_lines=preamble,
        extra_params={param_name: "api_key"},
        extra_imports=frozenset({"os"}),
    )


def _resolve_basic_auth(auth: NormalizedAuth) -> _AuthResolution:
    username_param = _find_auth_param(auth.parameters, "username")
    password_param = _find_auth_param(auth.parameters, "password")
    if (
        username_param is None
        or not username_param.value
        or password_param is None
        or not password_param.value
    ):
        return _unsupported_auth(
            AUTHENTICATION_NOT_SUPPORTED, "Basic Auth sem 'username' e/ou 'password' definidos"
        )

    username_variable = _extract_pure_variable_name(username_param.value)
    password_variable = _extract_pure_variable_name(password_param.value)
    if username_variable is None or password_variable is None:
        return _unsupported_auth(
            AUTHENTICATION_VALUE_NOT_RESOLVED,
            "usuário ou senha do Basic Auth não são referências de variável ({{...}}) resolvíveis",
        )

    username_env = _to_env_var_name(username_variable)
    password_env = _to_env_var_name(password_variable)
    preamble = (
        *_env_var_lookup_lines("username", username_env),
        *_env_var_lookup_lines("password", password_env),
        '    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()\n',
    )
    return _supported_auth(
        preamble_lines=preamble,
        extra_headers={"Authorization": 'f"Basic {credentials}"'},
        extra_imports=frozenset({"os", "base64"}),
    )


def _resolve_auth(auth: NormalizedAuth) -> _AuthResolution:
    if auth.auth_type in _NO_AUTH_TYPES:
        return _supported_auth()
    if auth.auth_type is AuthType.BEARER:
        return _resolve_bearer_auth(auth)
    if auth.auth_type is AuthType.API_KEY:
        return _resolve_api_key_auth(auth)
    if auth.auth_type is AuthType.BASIC:
        return _resolve_basic_auth(auth)

    label = auth.raw_type or auth.auth_type.value
    return _unsupported_auth(
        AUTHENTICATION_NOT_SUPPORTED, f"tipo de autenticação '{label}' ainda não suportado"
    )


class PlaywrightEndpointTestGenerator:
    # Implementação real do contrato EndpointTestGenerator (Parte 03),
    # substituindo o PlaceholderEndpointTestGenerator como gerador padrão
    # (Parte 07). Cobre o caso mais simples — GET, sem body, sem variáveis
    # não resolvidas — com um cenário positivo básico, incluindo
    # autenticação suportada (Bearer/API Key/Basic — Parte 12) via variável
    # de ambiente; qualquer endpoint fora disso cai no fallback (mesmo
    # conteúdo do PlaceholderEndpointTestGenerator, com um warning
    # explicando por quê), nunca em código que finja testar algo que não
    # testa.
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
            return _generate_positive_success_test(strategy, request, environment)

        fallback = self._fallback_generator.generate_endpoint(strategy, request, environment)
        warning = PlaywrightGenerationWarning(
            code=reason.code,
            message=f"Geração real ainda não suportada para este endpoint: {reason.message}.",
            endpoint=strategy.endpoint_source,
            scenario=None,
        )
        return replace(fallback, warnings=fallback.warnings + (warning,))


def _generate_positive_success_test(
    strategy: TestStrategy,
    request: NormalizedRequest,
    environment: PostmanEnvironment | None,
) -> GeneratedEndpointTest:
    slug = endpoint_source_to_slug(strategy.endpoint_source)
    function_name = f"test_{slug}_success"
    path = _relative_path(request.url)
    params = _build_query_params(request.url.query_parameters)
    header_resolution = _resolve_headers(
        request.headers, endpoint_source=strategy.endpoint_source, environment=environment
    )
    # Já sabido "supported" (gate em _unsupported_reason); recomputado aqui
    # (puro, sem efeito colateral) para obter o preâmbulo/headers/params
    # reais a renderizar — mesmo padrão de params/headers, que também são
    # recalculados em vez de repassados da checagem de suporte.
    auth_resolution = _resolve_auth(request.auth)

    # Auth tem precedência sobre um header/param regular de mesmo nome —
    # nunca colide com "Authorization"/"Content-Type" manuais (já excluídos
    # por _resolve_headers) e é a fonte mais estrutural quando o mesmo nome
    # aparecer nos dois lados (ex.: API Key em query com o mesmo nome de um
    # query parameter comum).
    all_params = {**params, **auth_resolution.extra_params}
    all_headers = {**header_resolution.headers, **auth_resolution.extra_headers}

    safe_request_name = _single_line(request.name or strategy.endpoint_source)
    safe_endpoint_source = _single_line(strategy.endpoint_source)
    auth_origin_note = (
        "sem autenticação"
        if request.auth.auth_type in _NO_AUTH_TYPES
        else "com autenticação suportada via variável de ambiente"
    )

    imports_block = "".join(f"import {name}\n" for name in sorted(auth_resolution.extra_imports))
    if imports_block:
        imports_block += "\n\n"

    preamble = "".join(auth_resolution.preamble_lines)
    if preamble:
        preamble += "\n"

    content = (
        f"{imports_block}"
        f"def {function_name}(api_context):\n"
        '    """\n'
        f"    Request: {safe_request_name}\n"
        f"    Method: {request.method}\n"
        f"    Endpoint: {safe_endpoint_source}\n"
        "    Scenario: success\n"
        "    Category: positive\n"
        f"    Origin: NormalizedRequest (GET simples, sem body, {auth_origin_note}, "
        "sem variáveis não resolvidas)\n"
        '    """\n'
        "\n"
        f"{preamble}"
        f"{_render_get_call(path, all_params, all_headers)}"
        "\n"
        "    assert response is not None\n"
    )

    return GeneratedEndpointTest(
        endpoint_source=strategy.endpoint_source,
        suggested_file_name=endpoint_source_to_file_name(strategy.endpoint_source),
        content=content,
        scenario_names=("success",),
        warnings=header_resolution.warnings,
        base_url=derive_base_url(request.url),
    )
