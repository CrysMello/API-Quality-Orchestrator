import json
import math
import re
from dataclasses import dataclass, replace

from typing import Any

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    AuthType,
    BodyMode,
    NormalizedAuth,
    NormalizedAuthParameter,
    NormalizedBody,
    NormalizedHeader,
    NormalizedQueryParameter,
    NormalizedRequest,
    NormalizedUrl,
    PostmanEnvironment,
    TestStrategy,
)
from api_quality_agent.generators.playwright.assertion_classification import (
    AssertionClassification,
)
from api_quality_agent.generators.playwright.assertion_precision import AssertionPrecision
from api_quality_agent.generators.playwright.endpoint_file_naming import (
    endpoint_source_to_file_name,
    endpoint_source_to_slug,
    is_parameterized_segment,
    parameterized_segment_key,
)
from api_quality_agent.generators.playwright.endpoint_test_generator import EndpointTestGenerator
from api_quality_agent.generators.playwright.generated_endpoint_test import GeneratedEndpointTest
from api_quality_agent.generators.playwright.placeholder_endpoint_test_generator import (
    PlaceholderEndpointTestGenerator,
)
from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)
from api_quality_agent.generators.playwright.variable_resolver import (
    VariableResolutionSession,
    env_var_lookup_lines,
    extract_pure_variable_name,
    multipart_file_env_var,
    sanitize_identifier,
)
from api_quality_agent.generators.playwright.warning_catalog import (
    ASSERTION_NOT_GENERATED,
    AUTHENTICATION_NOT_SUPPORTED,
    AUTHENTICATION_VALUE_NOT_RESOLVED,
    BODY_JSON_INVALID,
    BODY_NOT_SUPPORTED,
    BODY_STRUCTURE_NOT_DETERMINED,
    BROAD_STATUS_ASSERTION,
    DUPLICATE_HEADER_IGNORED,
    EXPECTED_STATUS_NOT_DEFINED,
    HEADER_VALUE_NOT_RESOLVED,
    HTTP_METHOD_NOT_SUPPORTED,
    INFORMATION_INSUFFICIENT,
    JSON_SCHEMA_REF_NOT_SUPPORTED,
    MULTIPART_FILE_NOT_RESOLVED,
    REQUEST_BODY_NOT_RENDERED,
    RESERVED_HEADER_OMITTED,
    SENSITIVE_HEADER_OMITTED,
    URL_NOT_RESOLVED,
)

_NO_AUTH_TYPES = (AuthType.NONE, AuthType.INHERIT, AuthType.UNKNOWN)
# Conversão conservadora de string -> tipo Python: só quando o round-trip
# str(int(value)) == value (exclui "007", "-0" etc., onde a representação
# original pode ser significativa, ex.: CEP/código) — nunca inventa um
# valor, só troca a representação do mesmo dado já presente no request.
_INTEGER_PATTERN = re.compile(r"^-?\d+$")
_BOOLEAN_LITERALS = {"true": True, "false": False}

# Nomes reservados (case-insensitive): nunca renderizados como header
# genérico, mesmo quando presentes e habilitados no NormalizedRequest.
# - authorization: sempre tratado como sensível — quando vem de
#   autenticação estruturada suportada (Parte 12), é a própria
#   _resolve_auth que escreve esse header, nunca um valor manual.
# - content-type: reservado para uma geração futura derivada do tipo de
#   body ("Content-Type específico por tipo de body" — não implementado
#   aqui) — evita duas fontes divergentes escrevendo o mesmo header.
_RESERVED_HEADER_NAMES = frozenset({"authorization", "content-type"})

# Parte 12 — autenticação suportada: Bearer Token, API Key (header ou
# query) e Basic Auth, só quando o(s) valor(es) relevante(s) forem uma
# referência pura a uma variável Postman ({{nome}}, nada mais na string) —
# nunca um segredo literal embutido na Collection. A partir da Parte 15, a
# resolução do NOME em si (literal do Environment/Collection vs variável de
# ambiente do sistema) passa pelo resolvedor central — ver
# variable_resolver.py.

# Parte 13 — método passa a incluir POST (além de GET), principalmente
# para poder carregar um body JSON.
#
# Parte 08A — mapeamento central e explícito dos métodos HTTP suportados,
# reaproveitando toda a montagem de argumentos já existente (URL/params/
# headers/auth/data/form/multipart) via _render_http_call: nenhum renderer
# independente por método. GET/POST/PUT/PATCH/DELETE/HEAD têm um método
# nativo dedicado em APIRequestContext (api_context.<verbo>); OPTIONS não
# tem equivalente nativo no Playwright e é servido via fetch(...,
# method="OPTIONS") — nunca fetch() liberado para qualquer verbo arbitrário
# (whitelist explícita, ver _SUPPORTED_METHODS abaixo). A presença de body é
# decidida por NormalizedRequest.body.has_content (ver _resolve_body),
# nunca pelo verbo em si — DELETE com body ou GET sem body são igualmente
# representáveis.
_NATIVE_HTTP_METHODS: dict[str, str] = {
    "GET": "get",
    "POST": "post",
    "PUT": "put",
    "PATCH": "patch",
    "DELETE": "delete",
    "HEAD": "head",
}
# Métodos sem chamada nativa em APIRequestContext, mas explicitamente
# habilitados via fetch(..., method=<verbo>) — só OPTIONS nesta etapa; um
# verbo fora desta lista (ex.: TRACE, CONNECT) continua caindo no fallback
# (HTTP_METHOD_NOT_SUPPORTED), nunca liberado silenciosamente por fetch().
_FETCH_ONLY_METHODS: frozenset[str] = frozenset({"OPTIONS"})
_SUPPORTED_METHODS: frozenset[str] = frozenset(_NATIVE_HTTP_METHODS) | _FETCH_ONLY_METHODS

# Body: RAW + Content-Type de JSON (Parte 13) e multipart/form-data (Parte
# 14) são suportados; qualquer outro modo (urlencoded, graphql, file) ou RAW
# sem Content-Type de JSON cai no fallback do endpoint inteiro — não dá pra
# montar uma requisição de verdade sem saber representar o corpo que ela
# deveria carregar.
#
# JSON declarado (RAW + Content-Type de JSON) mas o texto não é um JSON
# válido — nunca tentamos corrigir automaticamente; o endpoint inteiro
# cai no fallback em vez de gerar um payload aparentemente correto.
#
# Multipart/form-data (Parte 14): um campo de arquivo sem "key" não tem
# como virar uma variável de ambiente estável (AQO_UPLOAD_<NOME>) — o
# endpoint inteiro cai no fallback, nunca um nome de campo "adivinhado".
#
# Parte 16 — status HTTP: nunca inventa 200/201/qualquer código. Só gera
# `assert response.status == N` quando strategy.assertions já tem uma
# AssertionDefinition(STATUS_CODE) — a mesma TestStrategyEngine reaproveitada
# do caminho Postman, com a mesma prioridade de evidência (contexto/contrato
# > configuração > exemplo/contrato documentado > nenhuma, nunca "sucesso
# assumido"). Mesmo código de warning já usado pelo gerador Postman
# (postman_test_generator._translate_strategy_warnings) para o StrategyWarning
# "STATUS_CODE_AMBIGUOUS" — vocabulário de warning consistente entre os dois
# geradores.
#
# Parte 18 — body JSON: parseado (uma única vez, guardado em `body`) só
# quando há evidência de que a resposta É JSON (Content-Type compatível
# e/ou AssertionType.VALID_JSON_BODY) — nunca "qualquer JSON é considerado
# resposta funcionalmente correta" (regra 5): sem AssertionType.SCHEMA para
# dizer o tipo do nível superior (dict/list/escalar/null), o parse ainda
# acontece (prova que é JSON bem formado), mas nenhum isinstance é gerado —
# e este warning registra que a estrutura não pôde ser validada.
#
# Parte 23 — classificação EXACT/DERIVED/BROAD (AssertionPrecision, já
# existente desde a Parte 03): o nome literalmente previsto desde então
# (ver os fixtures de teste de Parte 03 em test_playwright_generation_
# contracts.py, que já usavam "BROAD_STATUS_ASSERTION" como exemplo) para
# quando a única asserção de status possível é a aproximação de sempre
# ("assert response is not None", Parte 16) — emitido JUNTO com
# EXPECTED_STATUS_NOT_DEFINED (que explica a ausência de evidência), nunca
# no lugar dele: um explica o "por quê", o outro classifica o "o quê" foi
# gerado no lugar.
#
# Parte 24 — todos os códigos acima (e os novos HTTP_METHOD_NOT_SUPPORTED/
# URL_NOT_RESOLVED/ASSERTION_NOT_GENERATED/INFORMATION_INSUFFICIENT) agora
# vêm de warning_catalog (catálogo estruturado e estável, única fonte de
# verdade) em vez de serem redefinidos aqui — ver import acima.


def _media_type_only(content_type: str) -> str:
    # "application/json; charset=utf-8" -> "application/json" — separa o
    # media type dos parâmetros (charset, boundary etc.) e normaliza caixa,
    # reaproveitado tanto para decidir se um body é JSON (Parte 13) quanto
    # para a asserção de Content-Type da resposta (Parte 17). Nunca compara
    # a string completa do header (Parte 17, regra 5) — só esta parte.
    return content_type.split(";", 1)[0].strip().lower()


def _is_json_content_type(content_type: str | None) -> bool:
    # Mesmo critério já usado por TestStrategyEngine._is_json_content_type
    # (domain/services/test_strategy_engine.py) — inclui variantes +json
    # (ex.: "application/vnd.api+json"), não só o media type genérico.
    if not content_type:
        return False
    media_type = _media_type_only(content_type)
    return media_type == "application/json" or media_type.endswith("+json")


def _single_line(text: str) -> str:
    # Nunca deve poder fechar a docstring triple-quoted onde é embutido nem
    # introduzir uma quebra de linha inesperada — endpoint_source/request.name
    # vêm do documento de origem (Collection/OpenAPI), não são controlados
    # por este código.
    return text.replace("\n", " ").replace("\r", " ").replace('"""', "'''")


# --- Classificação EXACT/DERIVED/BROAD (Parte 23) ---------------------------

# "Regra 5: não reclassificar uma expectativa apenas para reduzir warnings"
# — por isso a classificação de cada asserção mora ao lado de onde ela é
# decidida (mesma função que já resolve a asserção em si), nunca um
# classificador genérico separado que poderia "ajustar" o resultado depois.


def _classification_docstring_suffix(precision: AssertionPrecision) -> str:
    # Anexado à mesma linha de docstring que cada parte (16-22) já escreve
    # ("regra 4: a classificação deve chegar... aos metadados do cenário")
    # — nunca uma linha extra separada, para manter uma leitura só por
    # expectativa.
    return f" [{precision.value.upper()}]"


def _exact(assertion: str, *, source: str, justification: str) -> AssertionClassification:
    return AssertionClassification(
        assertion=assertion,
        precision=AssertionPrecision.EXACT,
        source=source,
        justification=justification,
    )


def _derived(assertion: str, *, source: str, justification: str) -> AssertionClassification:
    return AssertionClassification(
        assertion=assertion,
        precision=AssertionPrecision.DERIVED,
        source=source,
        justification=justification,
    )


def _broad(assertion: str, *, source: str, justification: str) -> AssertionClassification:
    return AssertionClassification(
        assertion=assertion,
        precision=AssertionPrecision.BROAD,
        source=source,
        justification=justification,
    )


@dataclass(frozen=True)
class _UnsupportedReason:
    code: str
    message: str
    # Mesmo vocabulário de UnresolvedVariable.location (Parte 24): "method",
    # "body", "auth", "url" ou "query" — onde no request o motivo do
    # fallback está.
    location: str


def _unsupported_reason(
    request: NormalizedRequest, environment: PostmanEnvironment | None
) -> tuple[_UnsupportedReason | None, VariableResolutionSession]:
    # Caso mais simples primeiro (Parte 07 em diante): GET ou POST (Parte
    # 13), body ausente, JSON válido ou multipart/form-data resolvível
    # (Parte 14), path/base URL/query resolvíveis pelo resolvedor central
    # (Parte 15), com autenticação suportada (Parte 12) ou nenhuma.
    # Qualquer coisa além disso ainda cai no fallback (placeholder +
    # warning) — nunca um código enganoso que pareça testar algo que não
    # testa de verdade.
    #
    # A sessão é sempre devolvida (mesmo quando o endpoint acaba caindo no
    # fallback) para que quem chama consiga registrar, no manifesto, as
    # variáveis que ficaram sem resolução (ex.: um path variable sem
    # default na Collection) — mesmo warning-level dado que o exemplo do
    # plano de ação mostra para "GET /users/{id}".
    session = VariableResolutionSession(environment=environment)

    method = (request.method or "").upper()
    if method not in _SUPPORTED_METHODS:
        return (
            _UnsupportedReason(
                HTTP_METHOD_NOT_SUPPORTED,
                f"método {request.method or 'desconhecido'} ainda não suportado",
                location="method",
            ),
            session,
        )

    body_reason = _unsupported_body_reason(request.body)
    if body_reason is not None:
        return body_reason, session

    auth_resolution = _resolve_auth(request.auth, session)
    if not auth_resolution.supported:
        assert auth_resolution.reason_code is not None  # garantido por _unsupported_auth
        assert auth_resolution.reason_message is not None
        return (
            _UnsupportedReason(
                auth_resolution.reason_code, auth_resolution.reason_message, location="auth"
            ),
            session,
        )

    # url_reason e query_reason cobrem, juntos, "URL" no sentido amplo (host,
    # path e query string) — mesmo código URL_NOT_RESOLVED para os dois,
    # location diferencia qual parte especificamente (Parte 24).
    url_reason = _unsupported_url_reason(request.url, session)
    if url_reason is not None:
        return _UnsupportedReason(URL_NOT_RESOLVED, url_reason, location="url"), session

    query_reason = _unsupported_query_reason(request.url.query_parameters, session)
    if query_reason is not None:
        return _UnsupportedReason(URL_NOT_RESOLVED, query_reason, location="query"), session

    return None, session


def _unsupported_body_reason(body: NormalizedBody) -> _UnsupportedReason | None:
    # "Tratar body vazio": has_content já é False para RAW com texto vazio
    # (ver PostmanRequestNormalizer._normalize_body) — nada a fazer aqui
    # além de tratar como "sem body", igual a uma request sem body nenhum.
    #
    # Resolução de variável (Parte 15) não muda SE o body é suportado —
    # só COMO ele é renderizado (ver _resolve_body) — por isso esta função
    # não recebe a sessão.
    if not body.has_content:
        return None

    if body.mode is BodyMode.FORMDATA:
        return _unsupported_multipart_reason(body)

    if body.mode is not BodyMode.RAW or not _is_json_content_type(body.content_type):
        return _UnsupportedReason(
            BODY_NOT_SUPPORTED,
            "body não declarado como JSON (modo ou Content-Type) ainda não suportado",
            location="body",
        )

    try:
        parsed = json.loads(body.text_content or "")
    except json.JSONDecodeError:
        # Nunca tenta corrigir automaticamente — só registra que não deu
        # para confiar no conteúdo declarado como JSON.
        return _UnsupportedReason(
            BODY_JSON_INVALID,
            "body declarado como JSON mas o conteúdo não é um JSON válido",
            location="body",
        )

    if _contains_non_finite_number(parsed):
        # Parte 08B: json.loads aceita NaN/Infinity/-Infinity (extensão
        # não-padrão), mas repr(float("nan")) produz o texto "nan" — um
        # NOME indefinido em Python, não um literal — o que quebraria a
        # suíte gerada só ao rodar de verdade. Nunca corrigido por
        # inferência (arredondar, remover o campo, trocar por null): o
        # endpoint inteiro cai no fallback, mesmo tratamento de
        # BODY_JSON_INVALID.
        return _UnsupportedReason(
            REQUEST_BODY_NOT_RENDERED,
            "body contém um valor numérico não finito (NaN/Infinity) sem representação "
            "como literal Python válido",
            location="body",
        )

    return None


def _contains_non_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        # bool é subclasse de int em Python — nunca cai no ramo de float
        # abaixo, mesmo critério já usado no restante do módulo.
        return False
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    if isinstance(value, list):
        return any(_contains_non_finite_number(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_non_finite_number(item) for item in value.values())
    return False


def _unsupported_multipart_reason(body: NormalizedBody) -> _UnsupportedReason | None:
    # Só campos habilitados são considerados — um campo de arquivo
    # desabilitado nunca é renderizado (mesmo critério de query/headers),
    # então sua ausência de "key" nunca impede a geração do endpoint.
    for field in body.fields:
        if field.disabled:
            continue
        if field.field_type == "file" and not field.key:
            return _UnsupportedReason(
                MULTIPART_FILE_NOT_RESOLVED,
                "campo de arquivo sem nome (key) não pode ser referenciado por variável de "
                "ambiente",
                location="body",
            )
    return None


# --- URL: path e base URL (Parte 15) -----------------------------------------


def _unsupported_url_reason(url: NormalizedUrl, session: VariableResolutionSession) -> str | None:
    # Protocolo com {{variável}} é extremamente raro e não tem um "local"
    # melhor para reportar do que base_url — tratado à parte para nunca
    # quebrar a montagem de host/path abaixo com um protocolo incompleto.
    if url.protocol and "{{" in url.protocol:
        session.mark_unresolved(url.protocol, "base_url")
        return "variáveis não resolvidas na URL ainda não são suportadas"

    # Ambos sempre rodam (nunca curto-circuita no primeiro None) para que
    # todo problema da URL seja reportado de uma vez, não só o primeiro.
    path_ok = _resolve_path_segments(url, session) is not None
    host_ok = _resolve_host_segments(url, session) is not None
    if not path_ok or not host_ok:
        return "variáveis não resolvidas na URL ainda não são suportadas"
    return None


def _resolve_path_segments(
    url: NormalizedUrl, session: VariableResolutionSession
) -> tuple[str, ...] | None:
    # Só prioridades 1 e 2 do resolvedor (environment/Collection, ambos já
    # conhecidos na geração) — o path= do Playwright é sempre uma string
    # simples neste gerador, nunca uma f-string; por isso um path variable
    # que só resolveria via variável de ambiente do sistema (prioridade 3)
    # continua "não resolvido" aqui, limitação deliberada desta parte.
    variables_by_key = {variable.key: variable.value for variable in url.variables if variable.key}

    resolved: list[str] = []
    ok = True
    for segment in url.path:
        pure_variable = extract_pure_variable_name(segment)
        if pure_variable is not None:
            # {{nome}} de verdade (variável Postman) — nunca "produzida por
            # outro teste"; resolve via Environment/Collection, senão fica
            # sem resolução (nunca inventa, nunca defere para runtime aqui).
            value = session.resolve_compile_time(
                pure_variable, collection_literal=variables_by_key.get(pure_variable)
            )
            if value is None:
                session.mark_unresolved(pure_variable, "path")
                ok = False
                continue
            resolved.append(value)
            continue

        if is_parameterized_segment(segment):
            # :nome ou {nome} (Postman/OpenAPI) — "produzida por outro
            # teste" por padrão (fora de escopo desta fase); só resolve
            # quando a própria Collection já declarou um default para essa
            # chave em url.variable[] (NormalizedUrlVariable.value).
            key = parameterized_segment_key(segment)
            literal = variables_by_key.get(key) if key else None
            if not literal:
                session.mark_unresolved(key or segment, "path")
                ok = False
                continue
            assert key is not None  # garantido por literal ser não-None acima
            session.resolved_variables[key] = literal
            resolved.append(literal)
            continue

        resolved.append(segment)

    return tuple(resolved) if ok else None


def _resolve_host_segments(
    url: NormalizedUrl, session: VariableResolutionSession
) -> tuple[str, ...] | None:
    # Sem equivalente de "valor literal da Collection" para host (não há
    # um NormalizedUrlVariable para segmentos de host) — só Environment ou
    # sem resolução; nunca defere para variável de ambiente do sistema aqui
    # (mesma limitação/motivo do path, ver _resolve_path_segments).
    resolved: list[str] = []
    ok = True
    for segment in url.host:
        pure_variable = extract_pure_variable_name(segment)
        if pure_variable is not None:
            value = session.resolve_compile_time(pure_variable)
            if value is None:
                session.mark_unresolved(pure_variable, "base_url")
                ok = False
                continue
            resolved.append(value)
            continue

        if "{{" in segment:
            # Referência parcial dentro de um segmento de host — nunca
            # interpolada (mesmo critério conservador de auth/multipart).
            session.mark_unresolved(segment, "base_url")
            ok = False
            continue

        resolved.append(segment)

    return tuple(resolved) if ok else None


def _relative_path_from_segments(segments: tuple[str, ...]) -> str:
    if segments:
        return "/" + "/".join(segments)
    return "/"


def _base_url_from_resolved(protocol: str | None, host_segments: tuple[str, ...]) -> str | None:
    # Mesmo critério de base_url.derive_base_url, mas a partir do host já
    # resolvido pelo resolvedor central — nunca inventa um host quando
    # protocol/host não estão presentes.
    if not protocol or not host_segments:
        return None
    return f"{protocol}://{'.'.join(host_segments)}"


def _unsupported_query_reason(
    query_parameters: tuple[NormalizedQueryParameter, ...],
    session: VariableResolutionSession,
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
        if "{{" in parameter.key:
            session.mark_unresolved(parameter.key, "query")
            return "variáveis não resolvidas em query parameters ainda não são suportadas"
        value = parameter.value or ""
        if "{{" in value and extract_pure_variable_name(value) is None:
            # Referência parcial — Parte 15 só resolve quando o valor
            # inteiro é {{nome}} (mesmo critério conservador de auth).
            session.mark_unresolved(value, "query")
            return "variáveis não resolvidas em query parameters ainda não são suportadas"

    return None


def _build_query_params(
    query_parameters: tuple[NormalizedQueryParameter, ...],
    session: VariableResolutionSession,
) -> dict[str, str]:
    # Só parâmetros habilitados (nunca gerados os desabilitados); ordem
    # preservada (mesma ordem da Collection, tuple já é determinística);
    # valor ausente/None tratado como string vazia — presente, mas vazio,
    # nunca omitido (distinto de "parâmetro ausente", que nunca chega aqui).
    # Valor já vem pré-renderizado como código Python (literal OU expressão
    # do resolvedor central — Parte 15), para poder conviver no mesmo dict
    # com parâmetros vindos de _resolve_auth, que também são expressões.
    params: dict[str, str] = {}
    for parameter in query_parameters:
        if parameter.disabled or not parameter.key:
            continue
        value = parameter.value or ""
        pure_variable = extract_pure_variable_name(value)
        if pure_variable is not None:
            params[parameter.key] = session.resolve(pure_variable)
            continue
        params[parameter.key] = _render_python_literal(_coerce_query_value(value))
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
    session: VariableResolutionSession,
) -> _HeaderResolution:
    # Só headers habilitados entram na consideração — os demais são
    # ignorados sem gerar warning (desabilitado é uma decisão explícita já
    # tomada na Collection, não uma omissão nossa a explicar).
    enabled = [h for h in headers if not h.disabled and h.key]

    resolved: dict[str, tuple[str, str]] = {}  # chave normalizada -> (nome original, expressão)
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

        pure_variable = extract_pure_variable_name(value) if "{{" in value else None
        if "{{" in key or ("{{" in value and pure_variable is None):
            # Parte 15: só uma referência PURA no valor resolve (ver
            # abaixo); nome de header com variável, ou valor com variável
            # parcial, continuam omitidos — mesmo critério conservador já
            # usado desde a Parte 11, agora também registrado no manifesto.
            session.mark_unresolved(key if "{{" in key else value, "header")
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

        # Expressão já pronta (Parte 15): literal escapado para um valor
        # comum, ou a variável local/literal resolvida pelo resolvedor
        # central quando o valor inteiro é {{nome}}.
        expression = session.resolve(pure_variable) if pure_variable is not None else _python_string_literal(value)
        resolved[lower_key] = (key, expression)

    ordered_headers = {original_key: expression for original_key, expression in resolved.values()}
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
        location="header",
        # Nunca o VALOR do header aqui, mesmo critério da mensagem acima
        # (regra 6) — só o nome, já seguro por construção.
        metadata=(("header", safe_key),),
    )


def _render_http_call(
    method: str,
    path: str,
    params: dict[str, str],
    headers: dict[str, str],
    data: str | None,
    multipart: dict[str, str] | None = None,
) -> str:
    # path/params/headers/data/multipart já chegam pré-renderizados como
    # código Python (cada valor é ou um literal escapado — _python_string_
    # literal/_render_python_literal/_render_json_literal — ou uma
    # expressão do resolvedor central/_resolve_auth, ex.: 'f"Bearer
    # {token}"', "request_body" ou o texto multi-linha de um FilePayload) —
    # este ponto só monta o texto, nunca decide como cada valor deve ser
    # representado. data e multipart são mutuamente exclusivos (JSON vs
    # multipart/form-data — nunca os dois ao mesmo tempo, ver _resolve_body).
    #
    # Parte 08A — method já chega em maiúsculas (mesma convenção de
    # _SUPPORTED_METHODS); um método nativo (_NATIVE_HTTP_METHODS) vira
    # api_context.<verbo>(...), qualquer outro habilitado (só OPTIONS nesta
    # etapa, ver _FETCH_ONLY_METHODS) vira api_context.fetch(..., method=
    # "OPTIONS") — o argumento method= é sempre explícito, nunca deixado no
    # default do fetch (que seria GET).
    native_method = _NATIVE_HTTP_METHODS.get(method)
    call = f"api_context.{native_method}" if native_method is not None else "api_context.fetch"
    method_kwarg = None if native_method is not None else _python_string_literal(method)

    path_literal = _python_string_literal(path)
    if method_kwarg is None and not params and not headers and data is None and not multipart:
        return f"    response = {call}({path_literal})\n"

    lines = [
        f"    response = {call}(\n",
        f"        {path_literal},\n",
    ]
    if method_kwarg is not None:
        lines.append(f"        method={method_kwarg},\n")
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
    if data is not None:
        # data=<dict Python> aciona a serialização JSON automática do
        # Playwright (Content-Type: application/json), sem precisar setar
        # o header manualmente nem serializar o texto aqui.
        lines.append(f"        data={data},\n")
    if multipart:
        # multipart=<dict Python> aciona o encoding multipart/form-data
        # automático do Playwright, boundary incluído — nunca escrito à
        # mão aqui (ver _render_file_payload_dict para o formato de cada
        # campo de arquivo).
        lines.append("        multipart={\n")
        for key, value in multipart.items():
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


# --- Body JSON (Parte 13) ----------------------------------------------------


@dataclass(frozen=True)
class _BodyResolution:
    # Linhas já formatadas para inserir no corpo da função, antes da
    # chamada api_context.<método>(...) — atribuição de request_body (JSON,
    # Parte 13). Vazio para multipart (Parte 14/15): todo preâmbulo de
    # campo/arquivo multipart passa a viver na sessão do resolvedor central,
    # compartilhada com os demais campos do endpoint.
    preamble_lines: tuple[str, ...]
    # Expressão Python já pronta para o argumento data= — sempre o nome da
    # variável local "request_body" quando há body JSON, None quando não há
    # (mesma convenção de valor pré-renderizado de headers/params/auth).
    data_expression: str | None = None
    # Dict chave -> expressão Python já pronta para o argumento multipart=
    # (Parte 14) — cada valor é ou um literal escapado, ou uma expressão do
    # resolvedor central (campo textual com {{variável}} resolvida), ou o
    # texto multi-linha de um FilePayload (campo de arquivo). None quando o
    # body não é multipart/form-data.
    multipart_fields: dict[str, str] | None = None


def _resolve_body(body: NormalizedBody, session: VariableResolutionSession) -> _BodyResolution:
    if not body.has_content:
        return _BodyResolution(preamble_lines=())

    if body.mode is BodyMode.FORMDATA:
        return _resolve_multipart_body(body, session)

    # Chegou aqui só depois de _unsupported_body_reason confirmar RAW +
    # Content-Type de JSON + JSON válido — reanalisar aqui é puro e sem
    # efeito colateral, mesmo padrão já usado por _resolve_auth.
    parsed = json.loads(body.text_content or "")
    rendered = _render_json_literal(parsed, "    ", session)
    return _BodyResolution(
        preamble_lines=(f"    request_body = {rendered}\n",),
        data_expression="request_body",
    )


def _render_json_literal(value: Any, base_indent: str, session: VariableResolutionSession) -> str:
    # value vem de json.loads: já preserva os tipos exatamente como
    # "Implementar" pede — null->None, true/false->bool, number->int/float,
    # string->str, object->dict (preserva ordem de inserção == ordem no
    # JSON original), array->list. Este renderizador só converte cada valor
    # Python já correto para o literal de código-fonte equivalente,
    # formatado deterministicamente (mesma indentação sempre, mesma ordem).
    #
    # Parte 15: uma string que é INTEIRAMENTE uma referência {{nome}} passa
    # pelo resolvedor central em vez de virar um literal de texto cru — o
    # mesmo critério conservador de sempre (só referência pura resolve;
    # "prefixo-{{nome}}" continua embutido como texto, igual à Parte 13).
    if value is None:
        return "None"
    if isinstance(value, bool):
        # bool antes de (int, float): bool é subclasse de int em Python.
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        pure_variable = extract_pure_variable_name(value)
        if pure_variable is not None:
            return session.resolve(pure_variable)
        return _python_string_literal(value)
    if isinstance(value, list):
        return _render_json_list(value, base_indent, session)
    if isinstance(value, dict):
        return _render_json_dict(value, base_indent, session)
    # Defensivo: json.loads nunca produz outro tipo além dos acima.
    return _python_string_literal(str(value))


def _render_json_dict(
    value: dict[str, Any], base_indent: str, session: VariableResolutionSession
) -> str:
    if not value:
        return "{}"
    child_indent = base_indent + "    "
    lines = [
        f"{child_indent}{_python_string_literal(key)}: "
        f"{_render_json_literal(item, child_indent, session)},"
        for key, item in value.items()
    ]
    return "{\n" + "\n".join(lines) + f"\n{base_indent}}}"


def _render_json_list(
    value: list[Any], base_indent: str, session: VariableResolutionSession
) -> str:
    if not value:
        return "[]"
    child_indent = base_indent + "    "
    lines = [
        f"{child_indent}{_render_json_literal(item, child_indent, session)}," for item in value
    ]
    return "[\n" + "\n".join(lines) + f"\n{base_indent}]"


# --- Multipart/form-data (Parte 14) ------------------------------------------


def _multipart_file_field_preamble(field_key: str, local_name: str) -> tuple[str, ...]:
    env_var = multipart_file_env_var(field_key)
    # Só o nome do campo (já seguro para aparecer em mensagem, mesmo padrão
    # de _header_warning) — nunca um caminho local — entra na mensagem de
    # falha; concatenado (+) em vez de f-string aqui para não precisar
    # escapar chaves do texto gerado.
    message_literal = _python_string_literal(
        f"Arquivo obrigatório não encontrado para o campo '{_single_line(field_key)}': "
    )
    return (
        # "Validar existência do arquivo em runtime" + "Gerar mensagem clara
        # quando o arquivo obrigatório estiver ausente": a variável de
        # ambiente e o próprio arquivo só são checados quando o teste roda,
        # nunca na geração — o mesmo teste gerado detecta tanto a variável
        # não configurada quanto o caminho configurado mas inexistente.
        *env_var_lookup_lines(f"{local_name}_path", env_var),
        f"    if not os.path.isfile({local_name}_path):\n",
        f"        pytest.fail({message_literal} + {local_name}_path)\n",
        f'    with open({local_name}_path, "rb") as {local_name}_fh:\n',
        f"        {local_name}_buffer = {local_name}_fh.read()\n",
    )


def _render_file_payload_dict(local_name: str, base_indent: str) -> str:
    # FilePayload do Playwright (name/mimeType/buffer) — nunca o caminho
    # local em si nem o conteúdo binário do arquivo aparecem no código
    # gerado, só referências às variáveis já lidas no preâmbulo (ver
    # _multipart_file_field_preamble); o conteúdo binário só existe em
    # memória quando o teste roda de verdade ("Não incorporar conteúdo
    # binário no código").
    child_indent = base_indent + "    "
    lines = [
        f'{child_indent}"name": os.path.basename({local_name}_path),',
        f'{child_indent}"mimeType": mimetypes.guess_type({local_name}_path)[0]',
        f'{child_indent}or "application/octet-stream",',
        f'{child_indent}"buffer": {local_name}_buffer,',
    ]
    return "{\n" + "\n".join(lines) + f"\n{base_indent}}}"


def _resolve_multipart_body(
    body: NormalizedBody, session: VariableResolutionSession
) -> _BodyResolution:
    # Chegou aqui só depois de _unsupported_multipart_reason confirmar que
    # todo campo de arquivo habilitado tem "key" — reanalisar aqui é puro e
    # sem efeito colateral, mesmo padrão já usado por _resolve_auth/_resolve_body.
    # Todo preâmbulo/import extra passa a viver na sessão compartilhada
    # (Parte 15) — nunca duplicado aqui.
    fields_code: dict[str, str] = {}

    for field in body.fields:
        if field.disabled or not field.key:
            continue

        if field.field_type == "file":
            local_name = session.resolve_file_field(field.key, _multipart_file_field_preamble)
            fields_code[field.key] = _render_file_payload_dict(local_name, "            ")
            continue

        # Campo textual: "Resolver variáveis em campos textuais" — só uma
        # referência pura ({{nome}}, nada mais na string) passa pelo
        # resolvedor central; qualquer outro valor (literal ou com
        # variável parcial) é embutido como texto, mesmo tratamento já
        # usado pelo body JSON (Parte 13) para conteúdo bruto.
        pure_variable = extract_pure_variable_name(field.value)
        if pure_variable is None:
            fields_code[field.key] = _python_string_literal(field.value or "")
            continue
        fields_code[field.key] = session.resolve(pure_variable)

    return _BodyResolution(preamble_lines=(), multipart_fields=fields_code or None)


# --- Autenticação (Parte 12) -------------------------------------------------


@dataclass(frozen=True)
class _AuthResolution:
    supported: bool
    reason_code: str | None
    reason_message: str | None
    # Chave -> expressão Python já pronta (não um literal cru) — ex.:
    # 'f"Bearer {token}"' ou "api_key" (nome de variável local materializada
    # na sessão via resolve_as_local_variable). Mesma convenção de valor
    # pré-renderizado usada por _build_query_params/_resolve_headers.
    extra_headers: dict[str, str]
    extra_params: dict[str, str]


def _unsupported_auth(code: str, message: str) -> _AuthResolution:
    return _AuthResolution(
        supported=False,
        reason_code=code,
        reason_message=message,
        extra_headers={},
        extra_params={},
    )


def _supported_auth(
    *,
    extra_headers: dict[str, str] | None = None,
    extra_params: dict[str, str] | None = None,
) -> _AuthResolution:
    return _AuthResolution(
        supported=True,
        reason_code=None,
        reason_message=None,
        extra_headers=extra_headers or {},
        extra_params=extra_params or {},
    )


def _find_auth_param(
    parameters: tuple[NormalizedAuthParameter, ...], key: str
) -> NormalizedAuthParameter | None:
    return next((parameter for parameter in parameters if parameter.key == key), None)


def _resolve_bearer_auth(auth: NormalizedAuth, session: VariableResolutionSession) -> _AuthResolution:
    token_param = _find_auth_param(auth.parameters, "token")
    if token_param is None or not token_param.value:
        return _unsupported_auth(
            AUTHENTICATION_NOT_SUPPORTED, "Bearer Token sem o parâmetro 'token' definido"
        )

    variable_name = extract_pure_variable_name(token_param.value)
    if variable_name is None:
        return _unsupported_auth(
            AUTHENTICATION_VALUE_NOT_RESOLVED,
            "valor do Bearer Token não é uma referência de variável ({{...}}) resolvível",
        )

    # "token" é sempre materializado como variável local (mesmo quando o
    # valor já é conhecido na geração via Environment) — o header é sempre
    # a mesma f-string, literal ou deferida (Parte 15).
    session.resolve_as_local_variable(variable_name, "token")
    return _supported_auth(extra_headers={"Authorization": 'f"Bearer {token}"'})


def _resolve_api_key_auth(auth: NormalizedAuth, session: VariableResolutionSession) -> _AuthResolution:
    key_param = _find_auth_param(auth.parameters, "key")
    value_param = _find_auth_param(auth.parameters, "value")
    if key_param is None or not key_param.value or value_param is None or not value_param.value:
        return _unsupported_auth(
            AUTHENTICATION_NOT_SUPPORTED, "API Key sem 'key' e/ou 'value' definidos"
        )

    variable_name = extract_pure_variable_name(value_param.value)
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

    session.resolve_as_local_variable(variable_name, "api_key")
    param_name = key_param.value

    if location == "header":
        return _supported_auth(extra_headers={param_name: "api_key"})
    return _supported_auth(extra_params={param_name: "api_key"})


def _resolve_basic_auth(auth: NormalizedAuth, session: VariableResolutionSession) -> _AuthResolution:
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

    username_variable = extract_pure_variable_name(username_param.value)
    password_variable = extract_pure_variable_name(password_param.value)
    if username_variable is None or password_variable is None:
        return _unsupported_auth(
            AUTHENTICATION_VALUE_NOT_RESOLVED,
            "usuário ou senha do Basic Auth não são referências de variável ({{...}}) resolvíveis",
        )

    session.resolve_as_local_variable(username_variable, "username")
    session.resolve_as_local_variable(password_variable, "password")
    if "credentials" not in session.seen_local_names:
        session.seen_local_names.add("credentials")
        session.preamble_lines.append(
            '    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()\n'
        )
        session.extra_imports.add("base64")

    return _supported_auth(extra_headers={"Authorization": 'f"Basic {credentials}"'})


def _resolve_auth(auth: NormalizedAuth, session: VariableResolutionSession) -> _AuthResolution:
    if auth.auth_type in _NO_AUTH_TYPES:
        return _supported_auth()
    if auth.auth_type is AuthType.BEARER:
        return _resolve_bearer_auth(auth, session)
    if auth.auth_type is AuthType.API_KEY:
        return _resolve_api_key_auth(auth, session)
    if auth.auth_type is AuthType.BASIC:
        return _resolve_basic_auth(auth, session)

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
        reason, session = _unsupported_reason(request, environment)
        if reason is None:
            return _generate_positive_success_test(strategy, request, environment)

        fallback = self._fallback_generator.generate_endpoint(strategy, request, environment)
        warning = PlaywrightGenerationWarning(
            code=reason.code,
            message=f"Geração real ainda não suportada para este endpoint: {reason.message}.",
            endpoint=strategy.endpoint_source,
            scenario=None,
            location=reason.location,
        )
        # Variáveis que ficaram sem resolução mesmo num endpoint que caiu
        # no fallback (Parte 15) — ex.: "GET /users/{id}" sem default de
        # "id" na Collection — ainda entram no manifesto (ver
        # default_playwright_test_suite_builder.py), mesmo sem um teste
        # real gerado para elas.
        return replace(
            fallback,
            warnings=fallback.warnings + (warning,),
            unresolved_variables=tuple(session.unresolved),
        )


# --- Status HTTP (Parte 16) --------------------------------------------------


@dataclass(frozen=True)
class _StatusAssertionResolution:
    # Linha já pronta para inserir no lugar da asserção final da função
    # gerada — nunca as duas ao mesmo tempo, sempre uma ou outra.
    assertion_line: str
    # Linha de docstring registrando a origem da expectativa nos metadados
    # do cenário ("Registrar a origem da expectativa de status") — já inclui
    # a tag [EXACT]/[BROAD] (Parte 23).
    docstring_note: str
    # Sempre ao menos um warning quando BROAD (regra 2 da Parte 23:
    # BROAD_STATUS_ASSERTION obrigatório) — vazio quando um status
    # confiável foi encontrado.
    warnings: tuple[PlaywrightGenerationWarning, ...]
    classification: AssertionClassification


def _find_status_code_assertion(strategy: TestStrategy) -> AssertionDefinition | None:
    return next(
        (a for a in strategy.assertions if a.assertion_type is AssertionType.STATUS_CODE), None
    )


def _resolve_status_assertion(strategy: TestStrategy, test_id: str) -> _StatusAssertionResolution:
    # "Não pode assumir que todo cenário positivo retorna 200 e não pode
    # inventar códigos HTTP": só gera `assert response.status == N` quando
    # strategy.assertions já traz um STATUS_CODE resolvido por evidência —
    # nunca um range (200 <= status < 300 / response.ok) que trataria erro
    # de autenticação/autorização/rota/mídia como sucesso só por cair na
    # mesma classe HTTP de outro código aceitável.
    #
    # P1.1: o `assert` original (linha exata que já existia) fica INTACTO
    # dentro do try — só envolvido para poder registrar o resultado, nunca
    # uma segunda comparação/validação paralela. P2.2: o caminho de falha
    # não relança mais a exceção — acumula em _assertion_failures e segue
    # para a próxima assertion (ver _generate_positive_success_test).
    assertion = _find_status_code_assertion(strategy)
    if assertion is not None:
        status_code = int(assertion.expected_value)
        reason = (
            f"Status HTTP {status_code} documentado explicitamente para este cenário "
            f"(evidência: {assertion.origin})."
        )
        classification = _exact("status", source=assertion.origin, justification=reason)
        assertion_line = (
            "    try:\n"
            f"        assert response.status == {status_code}\n"
            "    except AssertionError:\n"
            f"        _record_assertion_result(\n"
            f"            {test_id!r}, \"HTTP status\", {status_code}, response.status, "
            f"\"FAILED\", \"EXACT\", {reason!r},\n"
            "        )\n"
            # P2.2: assertions são avaliadas de forma independente — a falha
            # é registrada e a checagem seguinte continua, nunca um `raise`
            # que interromperia o teste antes das demais assertions
            # declaradas serem avaliadas (ver _assertion_failures ao final
            # da função gerada). Mensagem construída explicitamente (nunca
            # str(err)): o `assert` acima não tem mensagem própria, e
            # str(err) só seria enriquecido pelo assertion rewriting do
            # pytest quando o arquivo é coletado de verdade — nunca
            # garantido (ex.: execução via exec() em teste unitário).
            f'        _assertion_failures.append("HTTP status esperado {status_code}, recebido " + str(response.status))\n'
            "    else:\n"
            f"        _record_assertion_result(\n"
            f"            {test_id!r}, \"HTTP status\", {status_code}, response.status, "
            f"\"PASSED\", \"EXACT\", {reason!r},\n"
            "        )\n"
        )
        return _StatusAssertionResolution(
            assertion_line=assertion_line,
            docstring_note=(
                f"    Status: {status_code} (origem: {assertion.origin})"
                f"{_classification_docstring_suffix(classification.precision)}\n"
            ),
            warnings=(),
            classification=classification,
        )

    # Sem evidência confiável: mantém a validação temporária de sempre
    # (Bloco 3) — a aproximação "permitida quando não houver expectativa
    # exata" da Parte 23 (BROAD), já autorizada pelo desenho da Parte 16.
    # Nunca reclassifica para EXACT/DERIVED só para reduzir warnings (regra
    # 5): os dois warnings sempre coexistem aqui — um explica a AUSÊNCIA de
    # evidência (EXPECTED_STATUS_NOT_DEFINED, Parte 16), o outro classifica
    # o que foi gerado no lugar (BROAD_STATUS_ASSERTION, regra 2 da Parte
    # 23) — nunca um no lugar do outro.
    broad_reason = (
        "Nenhuma evidência de status disponível (estratégia de teste, Postman, OpenAPI, "
        "contrato ou exemplo); mantida a validação aproximada de que a resposta existe."
    )
    classification = _broad("status", source="none", justification=broad_reason)
    broad_assertion_line = (
        "    try:\n"
        "        assert response is not None\n"
        "    except AssertionError:\n"
        "        _record_assertion_result(\n"
        f"            {test_id!r}, \"HTTP status\", \"resposta presente\", \"ausente\", "
        f"\"FAILED\", \"BROAD\", {broad_reason!r},\n"
        "        )\n"
        # P2.2: ver nota da variante EXACT acima — nunca um `raise` aqui;
        # mensagem explícita, mesmo raciocínio (nunca str(err)).
        '        _assertion_failures.append("Nenhuma resposta HTTP foi recebida.")\n'
        "    else:\n"
        "        _record_assertion_result(\n"
        f"            {test_id!r}, \"HTTP status\", \"resposta presente\", \"presente\", "
        f"\"PASSED\", \"BROAD\", {broad_reason!r},\n"
        "        )\n"
    )
    return _StatusAssertionResolution(
        assertion_line=broad_assertion_line,
        docstring_note=(
            "    Status: não determinado — validação parcial "
            "(ver warning EXPECTED_STATUS_NOT_DEFINED)"
            f"{_classification_docstring_suffix(classification.precision)}\n"
        ),
        warnings=(
            PlaywrightGenerationWarning(
                code=EXPECTED_STATUS_NOT_DEFINED,
                message=(
                    "Nenhum status HTTP esperado pôde ser determinado a partir de evidência "
                    "disponível (estratégia de teste, Postman, OpenAPI, contrato ou exemplo); "
                    "a asserção de status não foi gerada — cenário mantido como validação "
                    "parcial ('assert response is not None')."
                ),
                endpoint=strategy.endpoint_source,
                scenario="success",
                location="status",
            ),
            PlaywrightGenerationWarning(
                code=BROAD_STATUS_ASSERTION,
                message=(
                    "Asserção de status classificada como BROAD (aproximação): sem status "
                    "exato disponível, o cenário valida apenas que a resposta não é nula."
                ),
                endpoint=strategy.endpoint_source,
                scenario="success",
                location="status",
            ),
        ),
        classification=classification,
    )


# --- Content-Type (Parte 17) --------------------------------------------------


@dataclass(frozen=True)
class _ContentTypeAssertionResolution:
    # Linhas extras a inserir no corpo da função, depois da asserção de
    # status — tupla vazia quando não há evidência ("Validar a existência
    # do header somente quando houver expectativa de body/content-type";
    # "não exigir Content-Type sem evidência" para respostas sem corpo,
    # ex.: 204 documentado). Ausência de evidência aqui nunca gera warning
    # (diferente de status, Parte 16): não ter Content-Type esperado é o
    # caso normal para uma resposta sem corpo, não um caso degradado.
    lines: tuple[str, ...]
    # Linha de docstring registrando a origem da expectativa — string vazia
    # quando não há evidência (nada a registrar).
    docstring_note: str
    # True só quando há evidência de Content-Type E ela é JSON-compatível
    # (application/json ou +json) — uma das duas fontes que autorizam o
    # parse do body na Parte 18 ("Content-Type compatível OU evidência
    # explícita de body JSON"). False tanto quando não há evidência quanto
    # quando ela aponta um media type não-JSON.
    is_json_compatible: bool = False
    # None quando não há evidência (nada gerado, nada a classificar —
    # ausência de Content-Type esperado é o caso normal para uma resposta
    # sem corpo, não um caso degradado; Parte 23 só classifica expectativas
    # realmente geradas).
    classification: AssertionClassification | None = None


def _find_content_type_assertion(strategy: TestStrategy) -> AssertionDefinition | None:
    return next(
        (a for a in strategy.assertions if a.assertion_type is AssertionType.CONTENT_TYPE), None
    )


def _resolve_content_type_assertion(
    strategy: TestStrategy, test_id: str
) -> _ContentTypeAssertionResolution:
    assertion = _find_content_type_assertion(strategy)
    if assertion is None:
        return _ContentTypeAssertionResolution(lines=(), docstring_note="")

    # Media type separado dos parâmetros (charset etc.) e normalizado em
    # caixa baixa já na geração — a asserção nunca compara a string
    # completa do header (regra 5: charset nunca causa falso negativo).
    expected_media_type = _media_type_only(str(assertion.expected_value))
    expected_literal = _python_string_literal(expected_media_type)
    is_json = _is_json_content_type(expected_media_type)
    kind = "JSON" if is_json else "não-JSON"

    # Mesmo texto que alimenta a docstring E cada _record_assertion_result
    # (P1.1, complementação) — uma única fonte para o "reason".
    reason = f"Content-Type documentado explicitamente (evidência: {assertion.origin})."
    # A expressão de normalização (.split(";")[0].strip().lower()) é lida de
    # novo nos dois _record_assertion_result — nunca guardada numa variável
    # nova para reuso, pra manter a linha do `assert` original 100% intacta
    # dentro do try (mesmo texto, mesma posição, mesmo comportamento de
    # introspecção do pytest). Ler a MESMA expressão de novo não é uma
    # segunda validação: é só reportar o que a comparação já decidiu.
    lines = (
        # .get(..., "") em vez de .get(...) cru: um header ausente em
        # runtime vira uma string vazia, que nunca bate com o media type
        # esperado — falha limpa na asserção seguinte, nunca um
        # AttributeError tentando chamar .split(...) em None.
        '    content_type = response.headers.get("content-type", "")\n',
        "    try:\n",
        # response.headers do Playwright já normaliza o NOME do header para
        # minúsculas (ver playwright._impl._network.RawHeaders) — "content-
        # type" cobre qualquer caixa original ("Content-Type",
        # "CONTENT-TYPE" etc.), satisfazendo a comparação case-insensitive
        # do nome pedida na regra 2. O VALOR também é normalizado aqui
        # (.lower()) antes de comparar.
        f'        assert content_type.split(";")[0].strip().lower() == {expected_literal}\n',
        "    except AssertionError:\n",
        "        _record_assertion_result(\n",
        f"            {test_id!r}, \"Content-Type\", {expected_literal}, "
        'content_type.split(";")[0].strip().lower(), "FAILED", "EXACT", '
        f"{reason!r},\n",
        "        )\n",
        # P2.2: assertions independentes — nunca um `raise` aqui (ver nota
        # em _resolve_status_assertion); mensagem explícita (nunca
        # str(err), mesmo raciocínio).
        f'        _assertion_failures.append("Content-Type esperado " + {expected_literal} + '
        '", recebido " + content_type.split(";")[0].strip().lower())\n',
        "    else:\n",
        "        _record_assertion_result(\n",
        f"            {test_id!r}, \"Content-Type\", {expected_literal}, "
        'content_type.split(";")[0].strip().lower(), "PASSED", "EXACT", '
        f"{reason!r},\n",
        "        )\n",
    )
    # Content-Type documentado explicitamente (JSON Schema/OpenAPI/contrato)
    # — sempre EXACT quando gerado; nunca uma aproximação (o media type
    # exigido é sempre o exato, nunca "algo parecido").
    classification = _exact("content_type", source=assertion.origin, justification=reason)
    docstring_note = (
        f"    Content-Type: {expected_media_type} [{kind}] (origem: {assertion.origin})"
        f"{_classification_docstring_suffix(classification.precision)}\n"
    )
    return _ContentTypeAssertionResolution(
        lines=lines,
        docstring_note=docstring_note,
        is_json_compatible=is_json,
        classification=classification,
    )


# --- Body JSON (Parte 18) -----------------------------------------------------


def _find_valid_json_body_assertion(strategy: TestStrategy) -> AssertionDefinition | None:
    return next(
        (a for a in strategy.assertions if a.assertion_type is AssertionType.VALID_JSON_BODY),
        None,
    )


def _find_schema_assertion(strategy: TestStrategy) -> AssertionDefinition | None:
    return next((a for a in strategy.assertions if a.assertion_type is AssertionType.SCHEMA), None)


# Nível superior apenas (regra 4) — nunca campos internos/schema completo
# (fora de escopo desta parte). bool é subclasse de int em Python
# (isinstance(True, int) é True) — sem o "and not isinstance(..., bool)" um
# body `true` passaria por engano como "integer"/"number".
_JSON_TOP_LEVEL_TYPE_EXPRESSIONS: dict[str, str] = {
    "object": "isinstance(body, dict)",
    "array": "isinstance(body, list)",
    "string": "isinstance(body, str)",
    "boolean": "isinstance(body, bool)",
    "integer": "isinstance(body, int) and not isinstance(body, bool)",
    "number": "isinstance(body, (int, float)) and not isinstance(body, bool)",
}


def _render_top_level_type_expression(json_type: Any) -> str | None:
    # None quando o "type" não foi documentado ou não é um dos valores
    # reconhecidos (ex.: uma lista de tipos do JSON Schema, "type":
    # ["object", "null"]) — nunca inventa uma asserção para uma estrutura
    # que não sabemos representar com segurança.
    if json_type == "null":
        return "body is None"
    if isinstance(json_type, str):
        return _JSON_TOP_LEVEL_TYPE_EXPRESSIONS.get(json_type)
    return None


@dataclass(frozen=True)
class _BodyJsonResolution:
    # Linhas extras a inserir depois da asserção de Content-Type — tupla
    # vazia quando não há evidência de que a resposta é JSON (regra 1:
    # "somente quando" Content-Type compatível OU AssertionType.
    # VALID_JSON_BODY presente; nunca por padrão).
    lines: tuple[str, ...]
    docstring_note: str
    warning: PlaywrightGenerationWarning | None
    extra_imports: frozenset[str]
    # None só quando nada foi gerado (sem evidência de JSON) — quando o
    # parse acontece, sempre há uma classificação: EXACT com o tipo do
    # nível superior conhecido, BROAD sem ele (regra 2 da Parte 23: mesmo
    # warning BODY_STRUCTURE_NOT_DETERMINED já emitido serve como "warning
    # equivalente ao tipo da asserção").
    classification: AssertionClassification | None = None


def _resolve_body_json_assertion(
    strategy: TestStrategy, content_type_resolution: _ContentTypeAssertionResolution
) -> _BodyJsonResolution:
    should_parse = content_type_resolution.is_json_compatible or (
        _find_valid_json_body_assertion(strategy) is not None
    )
    if not should_parse:
        return _BodyJsonResolution(
            lines=(), docstring_note="", warning=None, extra_imports=frozenset()
        )

    # Corpo vazio é uma categoria própria (regra 3: objeto/array/escalar/
    # null/vazio), nunca só tratado como "JSON inválido" — mensagem
    # específica e identificável antes de sequer tentar json.loads.
    # `body` fica disponível para reaproveitamento nas Partes 19-22 (regra
    # 6); .text() lido só uma vez, nunca duas chamadas a response.json()/
    # .text() (regra 7).
    lines: list[str] = [
        "    body_text = response.text()\n",
        "    if not body_text.strip():\n",
        '        pytest.fail("Corpo da resposta vazio; esperado um JSON válido.")\n',
        "    try:\n",
        "        body = json.loads(body_text)\n",
        "    except json.JSONDecodeError as error:\n",
        '        pytest.fail(f"Corpo da resposta não é um JSON válido: {error}")\n',
    ]

    schema_assertion = _find_schema_assertion(strategy)
    schema = schema_assertion.expected_value if schema_assertion is not None else None
    json_type = schema.get("type") if isinstance(schema, dict) else None
    type_expression = _render_top_level_type_expression(json_type)

    warning: PlaywrightGenerationWarning | None = None
    docstring_extra = ""
    if type_expression is not None:
        # "Não considerar qualquer JSON válido como resposta funcionalmente
        # correta" (regra 5): só o TIPO do nível superior é checado aqui —
        # nunca campos, valores ou schema completo.
        lines.append(f"    assert {type_expression}\n")
        assert schema_assertion is not None  # garantido por type_expression não-None
        classification = _exact(
            "body",
            source=schema_assertion.origin,
            justification=(
                f"Estrutura do nível superior ('{json_type}') documentada explicitamente no "
                f"schema (evidência: {schema_assertion.origin})."
            ),
        )
        docstring_extra = f" [estrutura: {json_type}, origem: {schema_assertion.origin}]"
    else:
        warning = PlaywrightGenerationWarning(
            code=BODY_STRUCTURE_NOT_DETERMINED,
            message=(
                "Há evidência de que a resposta é JSON, mas nenhum schema documentado "
                "define a estrutura do nível superior (objeto, array ou escalar); o body "
                "foi parseado, mas nenhuma asserção de estrutura foi gerada."
            ),
            endpoint=strategy.endpoint_source,
            scenario="success",
            location="body",
        )
        # BROAD: só prova que o body é JSON bem formado, sem saber a
        # estrutura — BODY_STRUCTURE_NOT_DETERMINED (acima) já é o "warning
        # equivalente ao tipo da asserção" pedido pela regra 2 da Parte 23.
        valid_json_body_assertion = _find_valid_json_body_assertion(strategy)
        source = valid_json_body_assertion.origin if valid_json_body_assertion is not None else "none"
        classification = _broad(
            "body",
            source=source,
            justification=(
                "Sem schema para determinar a estrutura do nível superior; validado apenas "
                "que o corpo é JSON bem formado."
            ),
        )

    docstring_note = (
        f"    Body: JSON parseado em `body`{docstring_extra}"
        f"{_classification_docstring_suffix(classification.precision)}\n"
    )

    return _BodyJsonResolution(
        lines=tuple(lines),
        docstring_note=docstring_note,
        warning=warning,
        extra_imports=frozenset({"json", "pytest"}),
        classification=classification,
    )


# --- Campos obrigatórios (Parte 19) ------------------------------------------

# Definidos como texto (não como funções Python normais deste módulo) para
# aparecer, ao pé da letra, no arquivo de teste gerado — nunca importados de
# api_quality_agent em runtime (a suíte gerada não depende do projeto que a
# gerou, mesmo princípio já seguido por conftest.py/demais partes). Os
# testes unitários deste módulo executam este MESMO texto (via exec) para
# provar o comportamento em runtime, nunca uma cópia que possa divergir.
# Cada helper é independente e só entra no arquivo gerado quando algo
# realmente o usa (ver _render_helpers_block) — nunca duplicado quando mais
# de uma parte (19 e 20) precisa do mesmo helper (ex.: _get_nested_value).
#
# _assert_required_field_present (Parte 19): navega node[path[0]][path[1]]...
# e falha com o caminho completo (ex.: "user.address.zipCode") assim que uma
# chave estiver ausente do dict corrente. Presença com valor null NUNCA
# falha — "diferenciar campo ausente de presente com null" — e interrompe a
# navegação (nada abaixo de um nó null para checar, "respeitar nullable").
# Um nó que não é mais um dict (não navegável) também interrompe sem falhar
# — nunca uma validação de tipo (fora de escopo da Parte 19).
#
# P1.1: test_id/reason só existem pra alimentar _record_assertion_result —
# a navegação/asserção em si (linhas dentro do try) é EXATAMENTE a mesma de
# antes, byte a byte, só envolvida num try/except que nunca muda o que é
# validado nem a mensagem de erro original (regra "nunca criar um mecanismo
# paralelo de validação"): o próprio AssertionError original é recapturado
# e relançado (bare "raise"), nunca substituído por um novo.
_ASSERT_REQUIRED_FIELD_PRESENT_SOURCE = (
    "def _assert_required_field_present(node, path, test_id, reason):\n"
    "    label = \".\".join(path)\n"
    "    try:\n"
    "        _node = node\n"
    "        for index, key in enumerate(path):\n"
    "            if not isinstance(_node, dict):\n"
    "                break\n"
    '            assert key in _node, "Campo obrigatório ausente: " + ".".join(path[: index + 1])\n'
    "            _node = _node[key]\n"
    "            if _node is None:\n"
    "                break\n"
    "    except AssertionError:\n"
    "        _record_assertion_result(\n"
    '            test_id, "required_field:" + label, "presente", "ausente", "FAILED",\n'
    "            \"EXACT\", reason,\n"
    "        )\n"
    "        raise\n"
    "    _record_assertion_result(\n"
    '        test_id, "required_field:" + label, "presente", "presente", "PASSED",\n'
    "        \"EXACT\", reason,\n"
    "    )\n"
)

# _get_nested_value (Partes 19 e 20): mesma navegação, mas devolve o valor
# (ou None) em vez de fazer asserção — usado só para localizar um campo
# array já comprovado presente, antes de iterar seus itens com segurança.
_GET_NESTED_VALUE_SOURCE = (
    "def _get_nested_value(node, path):\n"
    "    for key in path:\n"
    "        if not isinstance(node, dict) or key not in node:\n"
    "            return None\n"
    "        node = node[key]\n"
    "    return node\n"
)


@dataclass(frozen=True)
class _RequiredArrayField:
    # path até o campo array em si (ex.: ("order", "items")); campos que
    # cada item da lista deve ter, só quando o schema do "items" já
    # documenta o próprio "required" — nunca inventado.
    path: tuple[str, ...]
    item_required_fields: tuple[str, ...]


def _collect_required_structure(
    schema: Any, *, prefix: tuple[str, ...] = ()
) -> tuple[list[tuple[str, ...]], list[_RequiredArrayField]]:
    # "Consumir informações de required presentes em... JSON Schema" — nunca
    # inferido aqui a partir de exemplos (isso já aconteceria, se fosse o
    # caso, antes de chegar neste schema — este código só lê o "required"
    # já declarado). Só desce em propriedades cujo próprio schema documenta
    # type=object — um "type" ausente/ambíguo (ex.: lista de tipos do JSON
    # Schema, campo nullable "type": ["object", "null"]) nunca é assumido
    # como navegável, então nada é gerado para ele (regra 6, na prática:
    # nunca força presença de filhos de um campo cujo tipo não está
    # claramente documentado como objeto).
    required_paths: list[tuple[str, ...]] = []
    required_arrays: list[_RequiredArrayField] = []

    if not isinstance(schema, dict) or schema.get("type") != "object":
        return required_paths, required_arrays

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return required_paths, required_arrays

    required_names = schema.get("required")
    required_names = required_names if isinstance(required_names, list) else []

    for field_name in required_names:
        if not isinstance(field_name, str):
            continue
        path = (*prefix, field_name)
        required_paths.append(path)

        field_schema = properties.get(field_name)
        if not isinstance(field_schema, dict):
            continue
        field_type = field_schema.get("type")

        if field_type == "object":
            nested_paths, nested_arrays = _collect_required_structure(field_schema, prefix=path)
            required_paths.extend(nested_paths)
            required_arrays.extend(nested_arrays)
        elif field_type == "array":
            # "Para arrays: aplicar estrutura aos itens somente quando o
            # schema determinar" — só quando items é um schema de objeto
            # com "required" próprio; nunca aplica a estrutura do pai aos
            # itens nem inventa uma estrutura para eles.
            items_schema = field_schema.get("items")
            if isinstance(items_schema, dict) and items_schema.get("type") == "object":
                item_required = items_schema.get("required")
                if isinstance(item_required, list):
                    item_fields = tuple(f for f in item_required if isinstance(f, str))
                    if item_fields:
                        required_arrays.append(
                            _RequiredArrayField(path=path, item_required_fields=item_fields)
                        )

    return required_paths, required_arrays


def _render_required_array_field_lines(array_field: _RequiredArrayField) -> tuple[str, ...]:
    local_name = f"_{sanitize_identifier('_'.join(array_field.path))}_items"
    path_literal = repr(array_field.path)
    array_path_label = ".".join(array_field.path) + "[]"
    lines = [
        f"    {local_name} = _get_nested_value(body, {path_literal})\n",
        f"    if isinstance({local_name}, list):\n",
        f"        for _item in {local_name}:\n",
        "            if not isinstance(_item, dict):\n",
        "                continue\n",
        f"            for _field in {array_field.item_required_fields!r}:\n",
        # "Não acessar item[0] sem garantir existência": nunca indexado,
        # sempre um for — uma lista vazia simplesmente não itera (nunca
        # falha por causa disso).
        "                try:\n",
        f'                    assert _field in _item, "Campo obrigatório ausente: {array_path_label}." '
        '+ _field\n',
        # P2.2: assertions independentes — nunca interrompe a iteração nem
        # o restante do teste (ver nota em _resolve_status_assertion).
        "                except AssertionError as _error:\n",
        "                    _assertion_failures.append(str(_error))\n",
    ]
    return tuple(lines)


@dataclass(frozen=True)
class _RequiredFieldsResolution:
    # Nomes dos helpers module-level (ver _HELPER_SOURCES) que este bloco
    # precisa ter emitidos no arquivo — vazio quando não há campo
    # obrigatório a validar; a montagem final (uma vez só por arquivo,
    # deduplicada com o que a Parte 20 também precisar) acontece em
    # _generate_positive_success_test via _render_helpers_block.
    helper_names: frozenset[str]
    lines: tuple[str, ...]
    docstring_note: str
    classification: AssertionClassification | None = None


def _resolve_required_fields_assertion(
    strategy: TestStrategy, response_body_resolution: _BodyJsonResolution, test_id: str
) -> _RequiredFieldsResolution:
    # Só faz sentido checar campos dentro de `body` quando a Parte 18 já
    # garantiu que essa variável existe (evidência de JSON) — nunca gerado
    # isoladamente.
    empty = _RequiredFieldsResolution(helper_names=frozenset(), lines=(), docstring_note="")
    if not response_body_resolution.lines:
        return empty

    schema_assertion = _find_schema_assertion(strategy)
    schema = schema_assertion.expected_value if schema_assertion is not None else None
    required_paths, required_arrays = _collect_required_structure(schema)

    if not required_paths and not required_arrays:
        return empty

    assert schema_assertion is not None  # garantido por required_paths/arrays não vazios

    total = len(required_paths) + len(required_arrays)
    # Mesmo texto que alimenta a docstring E cada _record_assertion_result
    # (P1.1) — uma única fonte para o "reason", nunca duas versões da mesma
    # justificativa podendo divergir.
    reason = (
        f"{total} campo(s) declarado(s) como 'required' no schema documentado "
        f"(evidência: {schema_assertion.origin})."
    )

    lines: list[str] = []
    for path in required_paths:
        # P2.2: assertions independentes — _assert_required_field_present
        # continua levantando AssertionError normalmente (nenhuma mudança
        # no helper, nem nos testes que o exercitam isoladamente), mas o
        # call site agora captura a falha e continua para a próxima
        # assertion em vez de deixá-la propagar e interromper o teste.
        lines.append(
            "    try:\n"
            f"        _assert_required_field_present(body, {path!r}, {test_id!r}, {reason!r})\n"
            "    except AssertionError as _error:\n"
            "        _assertion_failures.append(str(_error))\n"
        )
    for array_field in required_arrays:
        lines.extend(_render_required_array_field_lines(array_field))

    # Sempre EXACT quando gerado: cada campo é literalmente listado em
    # "required" no schema — nunca uma aproximação (ausência de evidência
    # não gera nada, ver "empty" acima, nunca um BROAD "talvez obrigatório").
    classification = _exact("required_fields", source=schema_assertion.origin, justification=reason)
    docstring_note = (
        f"    Required fields: {total} campo(s) obrigatório(s) validados "
        f"(origem: {schema_assertion.origin})"
        f"{_classification_docstring_suffix(classification.precision)}\n"
    )

    helper_names = {"assert_required_field_present", "record_assertion_result"}
    if required_arrays:
        helper_names.add("get_nested_value")

    return _RequiredFieldsResolution(
        helper_names=frozenset(helper_names),
        lines=tuple(lines),
        docstring_note=docstring_note,
        classification=classification,
    )


# --- Tipos (Parte 20) ---------------------------------------------------------

# _assert_field_type: navega node/path (mesmo estilo de
# _assert_required_field_present) mas NUNCA falha por ausência — presença é
# escopo da Parte 19, não desta; um campo ausente simplesmente não tem nada
# para validar aqui. Quando presente, classifica o valor recebido (bool
# ANTES de int — regra 3: True/False nunca contam como integer/number,
# mesmo isinstance(True, int) sendo True em Python) e compara contra
# expected_type SEM NENHUMA CONVERSÃO (regra 1: "123" nunca é aceito como
# integer só porque parece um). "number" aceita tanto integer quanto number
# (JSON não distingue os dois; a distinção é só entre "integer" e "number"
# no próprio JSON Schema). null só passa quando nullable é True (regra 5) —
# senão é só mais um "tipo recebido" que não bate com o esperado. Mensagem
# sempre com campo (caminho completo), tipo esperado e tipo recebido
# (regra 6).
_ASSERT_FIELD_TYPE_SOURCE = (
    "def _assert_field_type(node, path, expected_type, nullable, test_id, name, reason):\n"
    "    for key in path[:-1]:\n"
    "        if not isinstance(node, dict) or key not in node:\n"
    "            return\n"
    "        node = node[key]\n"
    "    last_key = path[-1]\n"
    "    if not isinstance(node, dict) or last_key not in node:\n"
    "        return\n"
    "    value = node[last_key]\n"
    '    label = ".".join(path)\n'
    "    if value is None:\n"
    "        if nullable:\n"
    "            return\n"
    '        received = "null"\n'
    "    elif isinstance(value, bool):\n"
    '        received = "boolean"\n'
    "    elif isinstance(value, int):\n"
    '        received = "integer"\n'
    "    elif isinstance(value, float):\n"
    '        received = "number"\n'
    "    elif isinstance(value, str):\n"
    '        received = "string"\n'
    "    elif isinstance(value, list):\n"
    '        received = "array"\n'
    "    elif isinstance(value, dict):\n"
    '        received = "object"\n'
    "    else:\n"
    "        received = type(value).__name__\n"
    '    if expected_type == "number":\n'
    '        ok = received in ("integer", "number")\n'
    "    else:\n"
    "        ok = received == expected_type\n"
    # P1.1 (complementação): o `assert ok, (...)` original — mesmo texto,
    # mesma mensagem — fica intacto dentro do try; só é envolvido para
    # registrar o resultado antes de relançar a MESMA exceção (bare
    # "raise"), nunca uma segunda comparação. `name` chega pronto de quem
    # chama (o `path` sozinho não basta para nomear checagens de item de
    # array — ver _render_array_item_type_check_lines).
    "    try:\n"
    "        assert ok, (\n"
    "            \"Tipo inválido para o campo '\" + label + \"': esperado \" + expected_type\n"
    '            + ", recebido " + received + "."\n'
    "        )\n"
    "    except AssertionError:\n"
    "        _record_assertion_result(\n"
    '            test_id, name, expected_type, received, "FAILED", "EXACT", reason,\n'
    "        )\n"
    "        raise\n"
    "    _record_assertion_result(\n"
    '        test_id, name, expected_type, received, "PASSED", "EXACT", reason,\n'
    "    )\n"
)

# P1.1 (detalhamento de assertions): registra UMA assertion realmente
# checada em runtime — name/expected/actual/status/precision/reason — num
# arquivo NDJSON (mesmo padrão de _record_http_transaction em conftest.py:
# arquivo, nunca stdout; ausência da env var = captura desligada, sem erro).
# Vive aqui (per-arquivo, via _render_helpers_block), não em conftest.py,
# porque quem chama é o próprio corpo da função de teste gerada — mesmo
# lugar de _assert_field_type/_assert_required_field_present. NUNCA decide
# sozinho o que é secret: PlaywrightAdapter mascara expected/actual/reason
# depois de ler o arquivo, exatamente como já faz para a transação HTTP.
_RECORD_ASSERTION_RESULT_SOURCE = (
    "def _record_assertion_result(test_id, name, expected, actual, status, precision, reason):\n"
    "    results_path = os.environ.get(\"PLAYWRIGHT_ASSERTION_RESULTS_PATH\")\n"
    "    if not results_path:\n"
    "        return\n"
    "    entry = {\n"
    "        \"test_id\": test_id,\n"
    "        \"name\": name,\n"
    "        \"expected\": expected,\n"
    "        \"actual\": actual,\n"
    "        \"status\": status,\n"
    "        \"precision\": precision,\n"
    "        \"reason\": reason,\n"
    "    }\n"
    "    with open(results_path, \"a\", encoding=\"utf-8\") as handle:\n"
    "        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + \"\\n\")\n"
)

# Nome do helper -> texto; ordem estável de emissão (nunca depende de ordem
# de dict/set em runtime) para manter a geração determinística — usado
# tanto pela Parte 19 quanto pela Parte 20, deduplicado por nome quando as
# duas precisam do mesmo helper (get_nested_value). record_assertion_result
# vem primeiro porque os demais helpers (P1.1) chamam ele.
_HELPER_SOURCES: dict[str, str] = {
    "record_assertion_result": _RECORD_ASSERTION_RESULT_SOURCE,
    "get_nested_value": _GET_NESTED_VALUE_SOURCE,
    "assert_required_field_present": _ASSERT_REQUIRED_FIELD_PRESENT_SOURCE,
    "assert_field_type": _ASSERT_FIELD_TYPE_SOURCE,
}
_HELPER_ORDER: tuple[str, ...] = (
    "record_assertion_result",
    "get_nested_value",
    "assert_required_field_present",
    "assert_field_type",
)


def _render_helpers_block(needed: frozenset[str]) -> str:
    if not needed:
        return ""
    parts = [_HELPER_SOURCES[name] for name in _HELPER_ORDER if name in needed]
    # "\n\n" entre cada def e ao final: duas linhas em branco entre defs de
    # nível de módulo (mesma convenção do resto do arquivo gerado), inclusive
    # antes da função de teste que vem a seguir.
    return "\n\n".join(parts) + "\n\n"


def _normalize_field_type(raw_type: Any, nullable_flag: Any) -> tuple[str | None, bool]:
    # (tipo único reconhecido ou None, nullable) — cobre tanto "nullable":
    # true (OpenAPI 3.0) quanto "type": [<tipo>, "null"] (JSON Schema/
    # OpenAPI 3.1). Uma lista com MAIS de um tipo não-null é ambígua — nunca
    # inventa qual validar, devolve None (nada é gerado para esse campo).
    nullable = nullable_flag is True
    if isinstance(raw_type, str):
        return raw_type, nullable
    if isinstance(raw_type, list):
        non_null = [t for t in raw_type if isinstance(t, str) and t != "null"]
        if "null" in raw_type:
            nullable = True
        if len(non_null) == 1:
            return non_null[0], nullable
        return None, nullable
    return None, nullable


@dataclass(frozen=True)
class _FieldTypeCheck:
    path: tuple[str, ...]
    json_type: str
    nullable: bool


@dataclass(frozen=True)
class _ArrayItemTypeCheck:
    # path até o campo array em si; campos que cada item deve ter, só
    # quando o schema do "items" já documenta o tipo de cada um — nunca
    # aplica a estrutura do array aos itens nem inventa uma para eles
    # ("aplicar validação em estruturas aninhadas" só quando o schema
    # determinar, mesmo critério já usado pela Parte 19 para "required").
    array_path: tuple[str, ...]
    item_fields: tuple[tuple[str, str, bool], ...]  # (nome, tipo, nullable)


def _collect_field_types(
    schema: Any, *, prefix: tuple[str, ...] = ()
) -> tuple[list[_FieldTypeCheck], list[_ArrayItemTypeCheck], list[tuple[str, ...]]]:
    # "Não inferir tipo de negócio a partir de um único exemplo sem
    # classificação de evidência": só lê "type"/"nullable" já declarados no
    # schema resolvido (AssertionType.SCHEMA) — nunca deriva um tipo daqui
    # olhando um valor de exemplo. Só desce em propriedades cujo próprio
    # schema documenta type=object, mesmo critério da Parte 19.
    field_checks: list[_FieldTypeCheck] = []
    array_checks: list[_ArrayItemTypeCheck] = []
    # Parte 24: campos com "type" DECLARADO mas ambíguo (lista com 2+ tipos
    # não-null) ou de um formato não reconhecido — distinto de um campo que
    # simplesmente não declara "type" (caso normal, nunca reportado aqui):
    # aqui HÁ evidência, só não é suficiente para gerar a checagem sem
    # arbitrariedade (ver _normalize_field_type) — vira ASSERTION_NOT_
    # GENERATED em _resolve_field_types_assertion.
    ambiguous_paths: list[tuple[str, ...]] = []

    if not isinstance(schema, dict) or schema.get("type") != "object":
        return field_checks, array_checks, ambiguous_paths

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return field_checks, array_checks, ambiguous_paths

    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str) or not isinstance(field_schema, dict):
            continue
        path = (*prefix, field_name)
        raw_type = field_schema.get("type")
        json_type, nullable = _normalize_field_type(raw_type, field_schema.get("nullable"))
        if json_type is not None:
            field_checks.append(_FieldTypeCheck(path=path, json_type=json_type, nullable=nullable))
        elif raw_type is not None:
            ambiguous_paths.append(path)

        if json_type == "object":
            nested_fields, nested_arrays, nested_ambiguous = _collect_field_types(
                field_schema, prefix=path
            )
            field_checks.extend(nested_fields)
            array_checks.extend(nested_arrays)
            ambiguous_paths.extend(nested_ambiguous)
        elif json_type == "array":
            items_schema = field_schema.get("items")
            if isinstance(items_schema, dict) and items_schema.get("type") == "object":
                item_properties = items_schema.get("properties")
                item_fields: list[tuple[str, str, bool]] = []
                if isinstance(item_properties, dict):
                    for item_name, item_schema in item_properties.items():
                        if not isinstance(item_name, str) or not isinstance(item_schema, dict):
                            continue
                        item_type, item_nullable = _normalize_field_type(
                            item_schema.get("type"), item_schema.get("nullable")
                        )
                        if item_type is not None:
                            item_fields.append((item_name, item_type, item_nullable))
                if item_fields:
                    array_checks.append(
                        _ArrayItemTypeCheck(array_path=path, item_fields=tuple(item_fields))
                    )

    return field_checks, array_checks, ambiguous_paths


def _render_array_item_type_check_lines(
    array_check: _ArrayItemTypeCheck, test_id: str, reason: str
) -> tuple[str, ...]:
    local_name = f"_{sanitize_identifier('_'.join(array_check.array_path))}_items"
    path_literal = repr(array_check.array_path)
    lines = [
        f"    {local_name} = _get_nested_value(body, {path_literal})\n",
        f"    if isinstance({local_name}, list):\n",
        f"        for _item in {local_name}:\n",
        "            if not isinstance(_item, dict):\n",
        "                continue\n",
    ]
    array_label = ".".join(array_check.array_path)
    for item_name, item_type, item_nullable in array_check.item_fields:
        # `name` inclui o prefixo do array (ex.: "field_type:items[].id") —
        # o `path` sozinho ((item_name,), relativo a cada `_item`) não
        # carrega esse contexto, então precisa ser passado à parte.
        name_literal = repr(f"field_type:{array_label}[].{item_name}")
        # P2.2: assertions independentes (ver nota em
        # _resolve_field_types_assertion).
        lines.append(
            "            try:\n"
            f"                _assert_field_type(_item, {(item_name,)!r}, "
            f"{item_type!r}, {item_nullable!r}, {test_id!r}, {name_literal}, {reason!r})\n"
            "            except AssertionError as _error:\n"
            "                _assertion_failures.append(str(_error))\n"
        )
    return tuple(lines)


@dataclass(frozen=True)
class _FieldTypesResolution:
    helper_names: frozenset[str]
    lines: tuple[str, ...]
    docstring_note: str
    classification: AssertionClassification | None = None
    # Parte 24: ASSERTION_NOT_GENERATED por campo com "type" ambíguo — pode
    # existir mesmo quando classification é None (nenhum OUTRO campo pôde
    # ser validado, mas este em específico tinha evidência parcial).
    warnings: tuple[PlaywrightGenerationWarning, ...] = ()


def _ambiguous_field_type_warning(
    endpoint_source: str, path: tuple[str, ...]
) -> PlaywrightGenerationWarning:
    label = ".".join(path)
    return PlaywrightGenerationWarning(
        code=ASSERTION_NOT_GENERATED,
        message=(
            f"Campo '{label}' declara 'type' no schema, mas o valor não pôde ser traduzido "
            "numa checagem única (lista com 2+ tipos não-null, ou um valor de 'type' não "
            "reconhecido); nenhuma asserção de tipo foi gerada para ele."
        ),
        endpoint=endpoint_source,
        scenario="success",
        location="body",
        metadata=(("field", label),),
    )


def _resolve_field_types_assertion(
    strategy: TestStrategy, response_body_resolution: _BodyJsonResolution, test_id: str
) -> _FieldTypesResolution:
    # Mesmo pré-requisito da Parte 19: só existe `body` para inspecionar
    # quando a Parte 18 já provou (por evidência) que a resposta é JSON.
    empty = _FieldTypesResolution(helper_names=frozenset(), lines=(), docstring_note="")
    if not response_body_resolution.lines:
        return empty

    schema_assertion = _find_schema_assertion(strategy)
    schema = schema_assertion.expected_value if schema_assertion is not None else None
    field_checks, array_checks, ambiguous_paths = _collect_field_types(schema)

    warnings = tuple(
        _ambiguous_field_type_warning(strategy.endpoint_source, path) for path in ambiguous_paths
    )

    if not field_checks and not array_checks:
        return replace(empty, warnings=warnings)

    assert schema_assertion is not None  # garantido por field_checks/array_checks não vazios

    total = len(field_checks) + sum(len(a.item_fields) for a in array_checks)
    # Mesmo texto que alimenta a docstring E cada _record_assertion_result
    # (P1.1, complementação) — uma única fonte para o "reason".
    reason = (
        f"Tipo de {total} campo(s) documentado(s) explicitamente no schema "
        f"(evidência: {schema_assertion.origin})."
    )

    lines: list[str] = []
    for check in field_checks:
        name_literal = repr(f"field_type:{'.'.join(check.path)}")
        # P2.2: assertions independentes — mesmo raciocínio de
        # _resolve_required_fields_assertion (helper continua igual, o
        # call site é quem captura e continua).
        lines.append(
            "    try:\n"
            f"        _assert_field_type(body, {check.path!r}, {check.json_type!r}, "
            f"{check.nullable!r}, {test_id!r}, {name_literal}, {reason!r})\n"
            "    except AssertionError as _error:\n"
            "        _assertion_failures.append(str(_error))\n"
        )
    for array_check in array_checks:
        lines.extend(_render_array_item_type_check_lines(array_check, test_id, reason))

    # Sempre EXACT quando gerado: "type"/"nullable" vêm direto do schema,
    # nunca uma aproximação de tipo (ver _normalize_field_type — tipo
    # ambíguo/não documentado nunca gera checagem, então nunca chega BROAD
    # aqui).
    classification = _exact("field_types", source=schema_assertion.origin, justification=reason)
    docstring_note = (
        f"    Field types: {total} campo(s) validados (origem: {schema_assertion.origin})"
        f"{_classification_docstring_suffix(classification.precision)}\n"
    )

    helper_names = {"assert_field_type", "record_assertion_result"}
    if array_checks:
        helper_names.add("get_nested_value")

    return _FieldTypesResolution(
        helper_names=frozenset(helper_names),
        lines=tuple(lines),
        docstring_note=docstring_note,
        classification=classification,
        warnings=warnings,
    )


# --- JSON Schema (Parte 21) ---------------------------------------------------

# "JSON Schema é adicional, não substitutivo" — roda DEPOIS de status,
# Content-Type, body, campos obrigatórios e tipos (Partes 16-20), nunca no
# lugar deles; valida a MESMA evidência (AssertionType.SCHEMA) de forma
# completa/estrutural, via a lib jsonschema de verdade (não uma
# reimplementação própria) — nunca instalada como dependência do pacote
# principal (a geração em si não a usa), só como dependência de
# desenvolvimento (roda nos testes deste módulo) e, implicitamente, de
# quem for executar a suíte Playwright gerada (mesmo princípio de
# playwright/pytest, já exigidos por conftest.py sem serem dependência do
# projeto que gera a suíte). Código JSON_SCHEMA_REF_NOT_SUPPORTED importado
# de warning_catalog (Parte 24) — ver import no topo do arquivo.


def _render_schema_literal(value: Any, base_indent: str) -> str:
    # Deliberadamente independente de _render_json_literal (Parte 13/18):
    # aquele resolve {{variável}} via a sessão do resolvedor central (Parte
    # 15), o que nunca faz sentido para um JSON SCHEMA (estrutura/contrato,
    # não um valor de request) — aqui todo valor é sempre um literal puro,
    # nunca uma expressão.
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _python_string_literal(value)
    if isinstance(value, list):
        return _render_schema_literal_list(value, base_indent)
    if isinstance(value, dict):
        return _render_schema_literal_dict(value, base_indent)
    return _python_string_literal(str(value))


def _render_schema_literal_dict(value: dict[str, Any], base_indent: str) -> str:
    if not value:
        return "{}"
    child_indent = base_indent + "    "
    lines = [
        f"{child_indent}{_python_string_literal(key)}: "
        f"{_render_schema_literal(item, child_indent)},"
        for key, item in value.items()
    ]
    return "{\n" + "\n".join(lines) + f"\n{base_indent}}}"


def _render_schema_literal_list(value: list[Any], base_indent: str) -> str:
    if not value:
        return "[]"
    child_indent = base_indent + "    "
    lines = [f"{child_indent}{_render_schema_literal(item, child_indent)}," for item in value]
    return "[\n" + "\n".join(lines) + f"\n{base_indent}]"


def _find_unsupported_schema_refs(schema: Any) -> list[str]:
    # "$ref somente dentro do escopo efetivamente suportado": uma referência
    # LOCAL (começa com "#", ex.: "#/$defs/User") nunca sai do próprio
    # documento — a resolução embutida do jsonschema já lida com isso sem
    # tocar rede. Qualquer outra coisa (URL absoluta, caminho de arquivo)
    # nunca é buscada automaticamente (regra 7) — só reportada.
    found: list[str] = []
    _walk_schema_refs(schema, found)
    return found


def _walk_schema_refs(node: Any, found: list[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            found.append(ref)
        for value in node.values():
            _walk_schema_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_schema_refs(item, found)


@dataclass(frozen=True)
class _JsonSchemaResolution:
    lines: tuple[str, ...]
    docstring_note: str
    warning: PlaywrightGenerationWarning | None
    extra_imports: frozenset[str]
    classification: AssertionClassification | None = None


def _resolve_json_schema_assertion(
    strategy: TestStrategy, response_body_resolution: _BodyJsonResolution, test_id: str
) -> _JsonSchemaResolution:
    # Mesmo pré-requisito das Partes 19/20: só existe `body` para validar
    # quando a Parte 18 já provou (por evidência) que a resposta é JSON.
    empty = _JsonSchemaResolution(
        lines=(), docstring_note="", warning=None, extra_imports=frozenset()
    )
    if not response_body_resolution.lines:
        return empty

    schema_assertion = _find_schema_assertion(strategy)
    if schema_assertion is None or not isinstance(schema_assertion.expected_value, dict):
        return empty
    schema = schema_assertion.expected_value

    unsupported_refs = _find_unsupported_schema_refs(schema)
    if unsupported_refs:
        # "Não baixar conteúdo remoto automaticamente": a validação inteira
        # é pulada (nunca uma validação parcial que finja ter checado tudo)
        # e o motivo fica registrado tanto no warning quanto na docstring.
        refs_label = ", ".join(sorted(set(unsupported_refs)))
        warning = PlaywrightGenerationWarning(
            code=JSON_SCHEMA_REF_NOT_SUPPORTED,
            message=(
                "Schema contém referência(s) $ref fora do escopo suportado (não local ao "
                f"documento): {refs_label}. Validação por JSON Schema não foi gerada — nunca "
                "baixamos conteúdo remoto automaticamente."
            ),
            endpoint=strategy.endpoint_source,
            scenario="success",
            location="body",
            metadata=(("refs", refs_label),),
        )
        docstring_note = (
            "    JSON Schema: não validado — referência(s) $ref não suportada(s) "
            "(ver warning JSON_SCHEMA_REF_NOT_SUPPORTED)\n"
        )
        return _JsonSchemaResolution(
            lines=(), docstring_note=docstring_note, warning=warning, extra_imports=frozenset()
        )

    # Mesmo texto que alimenta a docstring E cada _record_assertion_result
    # (P1.1) — uma única fonte para o "reason".
    reason = (
        "Validação estrutural completa contra o schema documentado (biblioteca "
        f"jsonschema; evidência: {schema_assertion.origin})."
    )
    schema_literal = _render_schema_literal(schema, "    ")
    lines = (
        f"    _response_json_schema = {schema_literal}\n",
        "    try:\n",
        "        jsonschema.validate(instance=body, schema=_response_json_schema)\n",
        "    except jsonschema.exceptions.ValidationError as error:\n",
        # P1.1: registra a falha ANTES de pytest.fail(...) — que continua
        # exatamente igual a antes, nunca uma segunda validação: o único
        # jsonschema.validate(...) acima é quem decide, aqui só se
        # descreve o que ele já decidiu.
        "        _record_assertion_result(\n",
        f"            {test_id!r}, \"json_schema\", \"válido conforme schema documentado\",\n",
        '            "inválido: " + error.message, "FAILED", "EXACT", ' + repr(reason) + ",\n",
        "        )\n",
        # P2.2: assertions independentes — a validação de JSON Schema
        # (categoria própria, "json_schema") nunca pode impedir que outras
        # assertions declaradas (ex.: expected_value:id) sejam avaliadas.
        # Antes disto chamava pytest.fail(...) aqui, interrompendo o teste
        # imediatamente; agora só registra a falha e continua — a MESMA
        # mensagem (caminho/keyword/valor esperado/mensagem do validator,
        # regra 5) é preservada, só deixa de abortar a execução.
        "        _assertion_failures.append(\n",
        '            "Body não corresponde ao JSON Schema esperado — "\n',
        '            + "caminho: " + ".".join(str(part) for part in error.path)\n',
        '            + "; keyword: " + str(error.validator)\n',
        '            + "; esperado: " + repr(error.validator_value)\n',
        '            + "; mensagem: " + error.message\n',
        "        )\n",
        "    else:\n",
        "        _record_assertion_result(\n",
        f"            {test_id!r}, \"json_schema\", \"válido conforme schema documentado\",\n",
        '            "válido", "PASSED", "EXACT", ' + repr(reason) + ",\n",
        "        )\n",
    )
    # Sempre EXACT quando gerado: valida contra o schema REAL, completo —
    # nunca uma aproximação (o $ref não suportado, o único jeito de ficar
    # "menos que exato", já pula a validação inteira acima, nunca gera uma
    # checagem parcial disfarçada de completa).
    classification = _exact("json_schema", source=schema_assertion.origin, justification=reason)
    docstring_note = (
        f"    JSON Schema: validado (origem: {schema_assertion.origin})"
        f"{_classification_docstring_suffix(classification.precision)}\n"
    )

    return _JsonSchemaResolution(
        lines=lines,
        docstring_note=docstring_note,
        warning=None,
        extra_imports=frozenset({"jsonschema"}),
        classification=classification,
    )


# --- Valores esperados (Parte 22) --------------------------------------------

# Convenção própria (extensão "x-", mesmo mecanismo de vendor extension já
# padrão em OpenAPI/JSON Schema) para marcar "correlação comprovada entre
# request e response": um schema de resposta nunca é lido como "mesmo nome
# de campo = mesmo valor" por conta própria (isso seria regra de negócio
# inferida, explicitamente fora de escopo) — só quando o contrato declara
# EXPLICITAMENTE, neste campo, de qual campo do request o valor deveria vir.
_CORRELATION_KEYWORD = "x-source-request-field"


@dataclass(frozen=True)
class _ExpectedValueCheck:
    path: tuple[str, ...]
    # "const": value é o único valor aceito (igualdade). "enum": value é a
    # tupla de valores aceitos (inclusão) — só usado quando o schema declara
    # 2+ valores; um "enum" de 1 valor só é o mesmo que "const".
    kind: str
    value: Any


def _collect_expected_values(
    schema: Any, *, prefix: tuple[str, ...] = ()
) -> list[_ExpectedValueCheck]:
    # "Valor explícito no contrato": só const/enum, palavras-chave padrão do
    # próprio JSON Schema — nunca um valor "adivinhado" a partir de um
    # exemplo isolado (isso é exatamente o padrão já evitado desde a Parte
    # 19: presença/valor num exemplo não é obrigatoriedade nem expectativa).
    checks: list[_ExpectedValueCheck] = []
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return checks

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return checks

    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str) or not isinstance(field_schema, dict):
            continue
        path = (*prefix, field_name)

        if "const" in field_schema:
            checks.append(_ExpectedValueCheck(path=path, kind="const", value=field_schema["const"]))
        else:
            enum_values = field_schema.get("enum")
            if isinstance(enum_values, list) and len(enum_values) == 1:
                checks.append(_ExpectedValueCheck(path=path, kind="const", value=enum_values[0]))
            elif isinstance(enum_values, list) and len(enum_values) >= 2:
                checks.append(
                    _ExpectedValueCheck(path=path, kind="enum", value=tuple(enum_values))
                )

        if field_schema.get("type") == "object":
            checks.extend(_collect_expected_values(field_schema, prefix=path))

    return checks


@dataclass(frozen=True)
class _CorrelationCheck:
    # Só no nível superior da resposta (nunca aninhado — a correlação já é
    # a parte mais "arriscada" desta parte; manter o escopo raso reduz
    # ambiguidade sobre a que ela se refere).
    path: tuple[str, ...]
    source_field: str


def _collect_correlation_checks(
    schema: Any, request_json: dict[str, Any] | None
) -> list[_CorrelationCheck]:
    checks: list[_CorrelationCheck] = []
    if request_json is None or not isinstance(schema, dict) or schema.get("type") != "object":
        return checks
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return checks

    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str) or not isinstance(field_schema, dict):
            continue
        source_field = field_schema.get(_CORRELATION_KEYWORD)
        # "Não assumir que todo campo enviado será devolvido": só gera a
        # checagem quando o campo de origem REALMENTE foi enviado no
        # request_body desta requisição — nunca aponta para um campo que
        # nem existe no que foi enviado.
        if isinstance(source_field, str) and source_field in request_json:
            checks.append(_CorrelationCheck(path=(field_name,), source_field=source_field))

    return checks


def _collect_unmet_correlations(
    schema: Any, request_json: dict[str, Any] | None
) -> list[tuple[str, str]]:
    # Parte 24: o contrato DECLAROU a intenção (x-source-request-field
    # aponta um campo do request), mas não há informação suficiente em
    # tempo de geração para confirmá-la — o campo apontado nunca foi de
    # fato enviado nesta requisição (ou não há request_body JSON nenhum).
    # Distinto de "campo sem x-source-request-field" (não é uma intenção
    # declarada, nunca reportado) — mesmo escopo raso (só nível superior)
    # de _collect_correlation_checks.
    unmet: list[tuple[str, str]] = []
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return unmet
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return unmet

    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str) or not isinstance(field_schema, dict):
            continue
        source_field = field_schema.get(_CORRELATION_KEYWORD)
        if not isinstance(source_field, str):
            continue
        if request_json is None or source_field not in request_json:
            unmet.append((field_name, source_field))

    return unmet


def _unmet_correlation_warning(
    endpoint_source: str, field_name: str, source_field: str
) -> PlaywrightGenerationWarning:
    return PlaywrightGenerationWarning(
        code=INFORMATION_INSUFFICIENT,
        message=(
            f"Campo '{field_name}' declara correlação com 'x-source-request-field': "
            f"'{source_field}', mas este campo não foi enviado no corpo desta requisição; "
            "a correlação não pôde ser verificada e nenhuma asserção foi gerada para ela."
        ),
        endpoint=endpoint_source,
        scenario="success",
        location="body",
        metadata=(("field", field_name), ("source_field", source_field)),
    )


def _parsed_request_json_body(request_body: NormalizedBody) -> dict[str, Any] | None:
    # Mesmo critério de _unsupported_body_reason/_resolve_body (Parte 13) —
    # recomputado aqui (puro, sem efeito colateral) em vez de repassado,
    # mesmo padrão já usado por auth/headers/etc. neste arquivo. Quando isto
    # devolve um dict, Parte 13 garante que `request_body` existe no código
    # gerado (mesma condição, mesmo texto fonte).
    if request_body.mode is not BodyMode.RAW or not _is_json_content_type(request_body.content_type):
        return None
    try:
        parsed = json.loads(request_body.text_content or "")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _render_expected_value_check_lines(
    check: _ExpectedValueCheck, test_id: str, reason: str, precision: str
) -> tuple[str, ...]:
    label = ".".join(check.path)
    path_literal = repr(check.path)
    # "Valores sensíveis não podem aparecer na mensagem de falha" (regra 6):
    # nunca um `assert _value == X` cru — o assertion rewriting do pytest
    # exibiria os dois valores reais na falha mesmo com uma mensagem
    # customizada ao lado. Por isso sempre if/pytest.fail com uma mensagem
    # que cita só o CAMPO, nunca o valor esperado nem o recebido. A mesma
    # cautela NÃO se aplica ao registro de AssertionResult (P1.1,
    # complementação): é uma chamada de função comum, sem introspecção de
    # bytecode do pytest, escrita num arquivo NDJSON separado que já passa
    # pelo mascaramento de secrets do PlaywrightAdapter antes de persistir
    # — nunca aparece na saída/mensagem de falha do pytest.
    message = f"Valor inesperado para o campo '{label}' (ver contrato)."
    # P2.2: assertions independentes — a falha é registrada e acumulada
    # (nunca pytest.fail() aqui, que interromperia o teste antes das
    # demais assertions declaradas serem avaliadas); a mensagem em si
    # continua a mesma, citando só o CAMPO, nunca o valor esperado/recebido
    # (regra 6 preservada).
    fail_line = f"        _assertion_failures.append({_python_string_literal(message)})\n"
    name_literal = repr(f"expected_value:{label}")
    if check.kind == "const":
        expected_literal = _render_schema_literal(check.value, "    ")
        condition = f"_value != {expected_literal}"
    else:
        values_literal = (
            "(" + ", ".join(_render_schema_literal(item, "    ") for item in check.value) + ",)"
        )
        condition = f"_value not in {values_literal}"
        expected_literal = values_literal
    return (
        f"    _value = _get_nested_value(body, {path_literal})\n",
        f"    if {condition}:\n",
        "        _record_assertion_result(\n",
        f"            {test_id!r}, {name_literal}, {expected_literal}, _value, \"FAILED\", "
        f"{precision!r}, {reason!r},\n",
        "        )\n",
        fail_line,
        "    else:\n",
        "        _record_assertion_result(\n",
        f"            {test_id!r}, {name_literal}, {expected_literal}, _value, \"PASSED\", "
        f"{precision!r}, {reason!r},\n",
        "        )\n",
    )


def _render_correlation_check_lines(check: _CorrelationCheck, test_id: str, reason: str) -> tuple[str, ...]:
    label = ".".join(check.path)
    path_literal = repr(check.path)
    source_literal = _python_string_literal(check.source_field)
    # A mensagem inteira passa por _python_string_literal UMA vez só, nunca
    # concatenada com source_literal (que já é código-fonte, com aspas
    # próprias) dentro de outra string — evita aspas aninhadas quebrando a
    # sintaxe do arquivo gerado. Mesma cautela da regra 6: nunca um `assert`
    # cru comparando os dois valores — só o nome dos dois campos aparece na
    # mensagem. O "expected" aqui só é conhecido em runtime (é o próprio
    # valor enviado no request desta execução) — nunca um literal inventado
    # na geração. `request_body.get(...)` é lido de novo nos dois
    # _record_assertion_result (mesma expressão da comparação original,
    # nunca guardada numa variável nova) só para reportar o que a
    # comparação já decidiu — mesmo raciocínio do Content-Type acima: a
    # linha `if` original permanece exatamente a mesma, única responsável
    # pela decisão.
    message = (
        f"Valor do campo '{label}' não corresponde ao valor enviado em "
        f"request_body['{check.source_field}'] (ver contrato)."
    )
    name_literal = repr(f"correlation:{label}")
    # P2.2: assertions independentes — nunca pytest.fail() aqui (ver nota em
    # _render_expected_value_check_lines).
    return (
        f"    _value = _get_nested_value(body, {path_literal})\n",
        f"    if _value != request_body.get({source_literal}):\n",
        "        _record_assertion_result(\n",
        f"            {test_id!r}, {name_literal}, request_body.get({source_literal}), "
        f"_value, \"FAILED\", \"EXACT\", {reason!r},\n",
        "        )\n",
        f"        _assertion_failures.append({_python_string_literal(message)})\n",
        "    else:\n",
        "        _record_assertion_result(\n",
        f"            {test_id!r}, {name_literal}, request_body.get({source_literal}), "
        f"_value, \"PASSED\", \"EXACT\", {reason!r},\n",
        "        )\n",
    )


@dataclass(frozen=True)
class _ExpectedValuesResolution:
    helper_names: frozenset[str]
    lines: tuple[str, ...]
    docstring_note: str
    # Mais de uma entrada quando o cenário mistura precisões (ex.: um
    # "const" EXACT e um "enum" de 2+ valores DERIVED no mesmo endpoint) —
    # nunca uma única classificação "média" que esconderia a diferença
    # (regra 3 da Parte 23: BROAD/DERIVED nunca contam como EXACT).
    classifications: tuple[AssertionClassification, ...] = ()
    # Parte 24: INFORMATION_INSUFFICIENT por correlação declarada mas não
    # confirmável — pode existir mesmo quando classifications está vazio
    # (nenhuma OUTRA expectativa gerada, mas esta correlação em específico
    # tinha uma intenção declarada).
    warnings: tuple[PlaywrightGenerationWarning, ...] = ()


def _resolve_expected_values_assertion(
    strategy: TestStrategy,
    response_body_resolution: _BodyJsonResolution,
    request_body: NormalizedBody,
    test_id: str,
) -> _ExpectedValuesResolution:
    # Mesmo pré-requisito das Partes 19-21: só existe `body` para comparar
    # quando a Parte 18 já provou (por evidência) que a resposta é JSON.
    empty = _ExpectedValuesResolution(helper_names=frozenset(), lines=(), docstring_note="")
    if not response_body_resolution.lines:
        return empty

    schema_assertion = _find_schema_assertion(strategy)
    schema = schema_assertion.expected_value if schema_assertion is not None else None

    value_checks = _collect_expected_values(schema)
    request_json = _parsed_request_json_body(request_body)
    correlation_checks = _collect_correlation_checks(schema, request_json)
    unmet_correlations = _collect_unmet_correlations(schema, request_json)
    warnings = tuple(
        _unmet_correlation_warning(strategy.endpoint_source, field_name, source_field)
        for field_name, source_field in unmet_correlations
    )

    if not value_checks and not correlation_checks:
        return replace(empty, warnings=warnings)

    assert schema_assertion is not None  # garantido por value_checks/correlation_checks não vazios

    # "const"/enum de 1 valor e correlação são sempre EXACT (valor único,
    # sem ambiguidade); "enum" de 2+ valores é DERIVED (conjunto de valores
    # permitidos derivado do schema, sem um valor único garantido) — nunca
    # os dois misturados numa única classificação (regra 3).
    const_checks = [c for c in value_checks if c.kind == "const"]
    enum_checks = [c for c in value_checks if c.kind == "enum"]
    exact_total = len(const_checks) + len(correlation_checks)

    # Mesmo texto que alimenta a docstring/classification E cada
    # _record_assertion_result (P1.1, complementação) — uma única fonte por
    # grupo de precisão, nunca duas versões da mesma justificativa podendo
    # divergir. "enum_reason" só é usado quando enum_checks não é vazio,
    # mas é computado sempre (barato, string pura) para simplificar o loop
    # abaixo.
    exact_reason = (
        f"{exact_total} campo(s) com valor único documentado explicitamente "
        "('const'/'enum' de 1 valor) e/ou correlação comprovada com o request "
        "('x-source-request-field')."
    )
    enum_reason = (
        f"{len(enum_checks)} campo(s) com conjunto de valores permitidos derivado "
        "do 'enum' documentado no schema, sem valor único garantido."
    )

    lines: list[str] = []
    for check in value_checks:
        check_reason = exact_reason if check.kind == "const" else enum_reason
        check_precision = "EXACT" if check.kind == "const" else "DERIVED"
        lines.extend(
            _render_expected_value_check_lines(check, test_id, check_reason, check_precision)
        )
    for correlation_check in correlation_checks:
        lines.extend(_render_correlation_check_lines(correlation_check, test_id, exact_reason))

    classifications: list[AssertionClassification] = []
    tags: list[str] = []
    if exact_total:
        classifications.append(
            _exact("expected_values", source=schema_assertion.origin, justification=exact_reason)
        )
        tags.append(f"EXACT:{exact_total}")
    if enum_checks:
        classifications.append(
            _derived("expected_values", source=schema_assertion.origin, justification=enum_reason)
        )
        tags.append(f"DERIVED:{len(enum_checks)}")

    total = len(value_checks) + len(correlation_checks)
    docstring_note = (
        f"    Expected values: {total} campo(s) validados (origem: {schema_assertion.origin}) "
        f"[{', '.join(tags)}]\n"
    )

    return _ExpectedValuesResolution(
        helper_names=frozenset({"get_nested_value", "record_assertion_result"}),
        lines=tuple(lines),
        docstring_note=docstring_note,
        classifications=tuple(classifications),
        warnings=warnings,
    )


def _generate_positive_success_test(
    strategy: TestStrategy,
    request: NormalizedRequest,
    environment: PostmanEnvironment | None,
) -> GeneratedEndpointTest:
    slug = endpoint_source_to_slug(strategy.endpoint_source)
    function_name = f"test_{slug}_success"
    # Maiúsculas: mesma convenção de _SUPPORTED_METHODS/_NATIVE_HTTP_METHODS
    # (Parte 08A) — _render_http_call decide, a partir daqui, entre um
    # método nativo (api_context.<verbo>) e fetch(..., method=...).
    method = (request.method or "GET").upper()
    # test_id (P1.1): mesmo identificador usado por conftest.py
    # (_CURRENT_TEST_ID, via request.node.name em runtime) para correlacionar
    # test_id -> request/response -> assertions — aqui é literalmente o
    # nome da função que está sendo gerada agora, já conhecido nesta etapa.
    status_resolution = _resolve_status_assertion(strategy, function_name)
    content_type_resolution = _resolve_content_type_assertion(strategy, function_name)
    response_body_resolution = _resolve_body_json_assertion(strategy, content_type_resolution)
    required_fields_resolution = _resolve_required_fields_assertion(
        strategy, response_body_resolution, function_name
    )
    field_types_resolution = _resolve_field_types_assertion(
        strategy, response_body_resolution, function_name
    )
    json_schema_resolution = _resolve_json_schema_assertion(
        strategy, response_body_resolution, function_name
    )
    expected_values_resolution = _resolve_expected_values_assertion(
        strategy, response_body_resolution, request.body, function_name
    )

    # Já sabido "supported" (gate em _unsupported_reason); recomputado aqui
    # (puro, sem efeito colateral) com uma sessão NOVA — mesmo padrão já
    # usado para headers/params/auth, que também são recalculados em vez de
    # repassados da checagem de suporte. Ordem de resolução: path/base URL
    # (nunca geram preâmbulo — só literais já conhecidos na geração), depois
    # autenticação, query, headers e por fim o body — cada um pode acrescentar
    # preâmbulo/import à MESMA sessão, na ordem em que aparecem abaixo.
    session = VariableResolutionSession(environment=environment)

    path_segments = _resolve_path_segments(request.url, session)
    host_segments = _resolve_host_segments(request.url, session)
    assert path_segments is not None and host_segments is not None  # garantido pelo gate
    path = _relative_path_from_segments(path_segments)
    base_url = _base_url_from_resolved(request.url.protocol, host_segments)

    auth_resolution = _resolve_auth(request.auth, session)
    params = _build_query_params(request.url.query_parameters, session)
    header_resolution = _resolve_headers(
        request.headers, endpoint_source=strategy.endpoint_source, environment=environment, session=session
    )
    body_resolution = _resolve_body(request.body, session)

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
    if body_resolution.data_expression is not None:
        body_origin_note = "com body JSON"
    elif body_resolution.multipart_fields:
        body_origin_note = "com multipart/form-data"
    else:
        body_origin_note = "sem body"

    # "os"/"json" (P1.1): o helper record_assertion_result é usado
    # incondicionalmente (a asserção de status, EXACT ou BROAD, sempre
    # registra um resultado) — nunca dependente de outra condição.
    # "pytest" (P2.2): o bloco final de agregação (`if _assertion_failures:
    # pytest.fail(...)`) agora é emitido incondicionalmente — antes,
    # "pytest" só entrava via response_body_resolution.extra_imports
    # (parse de JSON), o que deixaria um NameError esperando pra acontecer
    # num cenário sem nenhuma assertion de corpo (ex.: só status code) cuja
    # única assertion falhasse.
    all_imports = (
        session.extra_imports
        | response_body_resolution.extra_imports
        | json_schema_resolution.extra_imports
        | {"os", "json", "pytest"}
    )
    imports_block = "".join(f"import {name}\n" for name in sorted(all_imports))
    if imports_block:
        imports_block += "\n\n"

    # Preâmbulo único (Parte 15): tudo que auth/query/headers/body
    # acrescentaram à sessão, na ordem em que cada resolução aconteceu
    # acima, seguido da montagem do body JSON (que não passa pela sessão —
    # ver _BodyResolution.preamble_lines).
    preamble = "".join(session.preamble_lines) + "".join(body_resolution.preamble_lines)
    if preamble:
        preamble += "\n"

    http_call = _render_http_call(
        method,
        path,
        all_params,
        all_headers,
        body_resolution.data_expression,
        body_resolution.multipart_fields,
    )

    # "record_assertion_result" (P1.1): mesma razão do "os"/"json" acima —
    # a asserção de status sempre o usa, nunca condicional a outra parte.
    helpers_block = _render_helpers_block(
        required_fields_resolution.helper_names
        | field_types_resolution.helper_names
        | expected_values_resolution.helper_names
        | {"record_assertion_result"}
    )

    content = (
        f"{imports_block}"
        f"{helpers_block}"
        f"def {function_name}(api_context):\n"
        '    """\n'
        f"    Request: {safe_request_name}\n"
        f"    Method: {request.method}\n"
        f"    Endpoint: {safe_endpoint_source}\n"
        "    Scenario: success\n"
        "    Category: positive\n"
        f"    Origin: NormalizedRequest ({request.method} simples, {body_origin_note}, "
        f"{auth_origin_note}, sem variáveis não resolvidas)\n"
        f"{status_resolution.docstring_note}"
        f"{content_type_resolution.docstring_note}"
        f"{response_body_resolution.docstring_note}"
        f"{required_fields_resolution.docstring_note}"
        f"{field_types_resolution.docstring_note}"
        f"{json_schema_resolution.docstring_note}"
        f"{expected_values_resolution.docstring_note}"
        '    """\n'
        "\n"
        f"{preamble}"
        f"{http_call}"
        # P2.2 (assertions independentes): cada categoria abaixo passou a
        # registrar sua falha e CONTINUAR em vez de interromper o teste
        # (`raise`/`pytest.fail()` imediato) — `_assertion_failures`
        # acumula uma entrada por assertion efetivamente avaliada e
        # reprovada; só ao final, depois de TODAS as categorias terem
        # rodado, o teste é marcado como falho (uma única vez), nunca antes
        # disso. Nenhuma assertion deixa de gerar seu próprio
        # AssertionResult por causa de outra ter falhado primeiro.
        "    _assertion_failures = []\n"
        "\n"
        f"{status_resolution.assertion_line}"
        f"{''.join(content_type_resolution.lines)}"
        f"{''.join(response_body_resolution.lines)}"
        f"{''.join(required_fields_resolution.lines)}"
        f"{''.join(field_types_resolution.lines)}"
        f"{''.join(json_schema_resolution.lines)}"
        f"{''.join(expected_values_resolution.lines)}"
        "\n"
        "    if _assertion_failures:\n"
        '        pytest.fail("Assertion(s) reprovada(s): " + "; ".join(_assertion_failures))\n'
    )

    warnings = (
        header_resolution.warnings
        + status_resolution.warnings
        + field_types_resolution.warnings
        + expected_values_resolution.warnings
    )
    if response_body_resolution.warning is not None:
        warnings = warnings + (response_body_resolution.warning,)
    if json_schema_resolution.warning is not None:
        warnings = warnings + (json_schema_resolution.warning,)

    # Parte 23: uma entrada por categoria de asserção que de fato gerou
    # algo — nunca uma entrada "vazia" para uma categoria sem evidência
    # nenhuma (content_type sem evidência, json_schema pulado por $ref não
    # suportado etc. simplesmente não aparecem aqui).
    assertion_classifications = tuple(
        classification
        for classification in (
            status_resolution.classification,
            content_type_resolution.classification,
            response_body_resolution.classification,
            required_fields_resolution.classification,
            field_types_resolution.classification,
            json_schema_resolution.classification,
            *expected_values_resolution.classifications,
        )
        if classification is not None
    )

    return GeneratedEndpointTest(
        endpoint_source=strategy.endpoint_source,
        suggested_file_name=endpoint_source_to_file_name(strategy.endpoint_source),
        content=content,
        scenario_names=("success",),
        warnings=warnings,
        base_url=base_url,
        required_environment_variables=tuple(sorted(session.required_environment_variables)),
        resolved_variables=tuple(sorted(session.resolved_variables.items())),
        unresolved_variables=(),
        assertion_classifications=assertion_classifications,
    )
