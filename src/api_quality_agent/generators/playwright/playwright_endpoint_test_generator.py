import json
import re
from dataclasses import dataclass, replace

from typing import Any

from api_quality_agent.domain.models import (
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

# Parte 13 — método passa a incluir POST (além de GET), principalmente
# para poder carregar um body JSON. PUT/PATCH/DELETE continuam fora do
# escopo (nenhum exemplo/critério desta parte os menciona) — mesma
# incrementalidade conservadora das partes anteriores.
_SUPPORTED_METHODS = frozenset({"GET", "POST"})

# Body: RAW + Content-Type de JSON (Parte 13) e multipart/form-data (Parte
# 14) são suportados; qualquer outro modo (urlencoded, graphql, file) ou RAW
# sem Content-Type de JSON cai no fallback do endpoint inteiro — não dá pra
# montar uma requisição de verdade sem saber representar o corpo que ela
# deveria carregar.
BODY_NOT_SUPPORTED = "BODY_NOT_SUPPORTED"
# JSON declarado (RAW + Content-Type de JSON) mas o texto não é um JSON
# válido — nunca tentamos corrigir automaticamente; o endpoint inteiro
# cai no fallback em vez de gerar um payload aparentemente correto.
BODY_JSON_INVALID = "BODY_JSON_INVALID"
# Multipart/form-data (Parte 14): um campo de arquivo sem "key" não tem
# como virar uma variável de ambiente estável (AQO_UPLOAD_<NOME>) — o
# endpoint inteiro cai no fallback, nunca um nome de campo "adivinhado".
MULTIPART_FILE_NOT_RESOLVED = "MULTIPART_FILE_NOT_RESOLVED"


def _is_json_content_type(content_type: str | None) -> bool:
    # Mesmo critério já usado por TestStrategyEngine._is_json_content_type
    # (domain/services/test_strategy_engine.py) — ignora parâmetros do
    # cabeçalho (ex.: "; charset=utf-8").
    if not content_type:
        return False
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


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
    # Caso mais simples primeiro (Parte 07 em diante): GET ou POST (Parte
    # 13), body ausente, JSON válido ou multipart/form-data resolvível
    # (Parte 14), sem variáveis de path não resolvidas, com autenticação
    # suportada (Parte 12) ou nenhuma. Qualquer coisa além disso ainda cai
    # no fallback (placeholder + warning) — nunca um código enganoso que
    # pareça testar algo que não testa de verdade.
    method = (request.method or "").upper()
    if method not in _SUPPORTED_METHODS:
        return _UnsupportedReason(
            ENDPOINT_NOT_SUPPORTED_YET,
            f"método {request.method or 'desconhecido'} ainda não suportado",
        )

    body_reason = _unsupported_body_reason(request.body)
    if body_reason is not None:
        return body_reason

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


def _unsupported_body_reason(body: NormalizedBody) -> _UnsupportedReason | None:
    # "Tratar body vazio": has_content já é False para RAW com texto vazio
    # (ver PostmanRequestNormalizer._normalize_body) — nada a fazer aqui
    # além de tratar como "sem body", igual a uma request sem body nenhum.
    if not body.has_content:
        return None

    if body.mode is BodyMode.FORMDATA:
        return _unsupported_multipart_reason(body)

    if body.mode is not BodyMode.RAW or not _is_json_content_type(body.content_type):
        return _UnsupportedReason(
            BODY_NOT_SUPPORTED,
            "body não declarado como JSON (modo ou Content-Type) ainda não suportado",
        )

    try:
        json.loads(body.text_content or "")
    except json.JSONDecodeError:
        # Nunca tenta corrigir automaticamente — só registra que não deu
        # para confiar no conteúdo declarado como JSON.
        return _UnsupportedReason(
            BODY_JSON_INVALID, "body declarado como JSON mas o conteúdo não é um JSON válido"
        )

    return None


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
            )
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


def _render_http_call(
    method: str,
    path: str,
    params: dict[str, str],
    headers: dict[str, str],
    data: str | None,
    multipart: dict[str, str] | None = None,
) -> str:
    # params/headers/data/multipart já chegam pré-renderizados como código
    # Python (cada valor é ou um literal escapado — _python_string_literal/
    # _render_python_literal/_render_json_literal — ou uma expressão de
    # _resolve_auth/_resolve_body, ex.: 'f"Bearer {token}"', "request_body"
    # ou o texto multi-linha de um FilePayload) — este ponto só monta o
    # texto, nunca decide como cada valor deve ser representado. data e
    # multipart são mutuamente exclusivos (JSON vs multipart/form-data —
    # nunca os dois ao mesmo tempo, ver _resolve_body).
    call = f"api_context.{method}"
    if not params and not headers and data is None and not multipart:
        return f"    response = {call}({_python_string_literal(path)})\n"

    lines = [
        f"    response = {call}(\n",
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
    # Parte 13) e/ou leitura de variáveis de ambiente e arquivos (multipart,
    # Parte 14).
    preamble_lines: tuple[str, ...]
    # Expressão Python já pronta para o argumento data= — sempre o nome da
    # variável local "request_body" quando há body JSON, None quando não há
    # (mesma convenção de valor pré-renderizado de headers/params/auth).
    data_expression: str | None = None
    # Dict chave -> expressão Python já pronta para o argumento multipart=
    # (Parte 14) — cada valor é ou um literal escapado, ou o nome de uma
    # variável local (campo textual com {{variável}} resolvida), ou o texto
    # multi-linha de um FilePayload (campo de arquivo). None quando o body
    # não é multipart/form-data.
    multipart_fields: dict[str, str] | None = None
    # Imports extras exigidos pelo preâmbulo acima (ex.: "pytest" para
    # pytest.fail, "mimetypes" para adivinhar o Content-Type do arquivo) —
    # union com auth_resolution.extra_imports em _generate_positive_success_test.
    extra_imports: frozenset[str] = frozenset()


def _resolve_body(body: NormalizedBody) -> _BodyResolution:
    if not body.has_content:
        return _BodyResolution(preamble_lines=())

    if body.mode is BodyMode.FORMDATA:
        return _resolve_multipart_body(body)

    # Chegou aqui só depois de _unsupported_body_reason confirmar RAW +
    # Content-Type de JSON + JSON válido — reanalisar aqui é puro e sem
    # efeito colateral, mesmo padrão já usado por _resolve_auth.
    parsed = json.loads(body.text_content or "")
    rendered = _render_json_literal(parsed, "    ")
    return _BodyResolution(
        preamble_lines=(f"    request_body = {rendered}\n",),
        data_expression="request_body",
    )


def _render_json_literal(value: Any, base_indent: str) -> str:
    # value vem de json.loads: já preserva os tipos exatamente como
    # "Implementar" pede — null->None, true/false->bool, number->int/float,
    # string->str, object->dict (preserva ordem de inserção == ordem no
    # JSON original), array->list. Este renderizador só converte cada valor
    # Python já correto para o literal de código-fonte equivalente,
    # formatado deterministicamente (mesma indentação sempre, mesma ordem).
    if value is None:
        return "None"
    if isinstance(value, bool):
        # bool antes de (int, float): bool é subclasse de int em Python.
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _python_string_literal(value)
    if isinstance(value, list):
        return _render_json_list(value, base_indent)
    if isinstance(value, dict):
        return _render_json_dict(value, base_indent)
    # Defensivo: json.loads nunca produz outro tipo além dos acima.
    return _python_string_literal(str(value))


def _render_json_dict(value: dict[str, Any], base_indent: str) -> str:
    if not value:
        return "{}"
    child_indent = base_indent + "    "
    lines = [
        f"{child_indent}{_python_string_literal(key)}: "
        f"{_render_json_literal(item, child_indent)},"
        for key, item in value.items()
    ]
    return "{\n" + "\n".join(lines) + f"\n{base_indent}}}"


def _render_json_list(value: list[Any], base_indent: str) -> str:
    if not value:
        return "[]"
    child_indent = base_indent + "    "
    lines = [f"{child_indent}{_render_json_literal(item, child_indent)}," for item in value]
    return "[\n" + "\n".join(lines) + f"\n{base_indent}]"


# --- Multipart/form-data (Parte 14) ------------------------------------------

# Campo de multipart pode ter espaço/símbolo no "key" (ex.: "Profile
# Picture") — nunca usado cru como identificador Python nem como sufixo de
# variável de ambiente.
_INVALID_IDENTIFIER_CHARS = re.compile(r"[^a-z0-9_]+")


def _sanitize_field_identifier(name: str) -> str:
    # Mesmo alfabeto seguro usado por endpoint_file_naming._sanitize_segment
    # (snake_case + só [a-z0-9_]) — reservas de nome quando sobra vazio (ex.:
    # "key" só com símbolos) nunca deixam o identificador em branco.
    #
    # Limitação conhecida, deliberadamente não tratada (fora do escopo desta
    # parte, sem exemplo/critério que peça isso): dois campos cujo "key" (ou
    # variável) sanitiza para o mesmo slug (ex.: "Profile Picture" e
    # "profile_picture") colidiriam no mesmo nome de variável local — o
    # último declarado prevalece, sem aviso.
    slug = _INVALID_IDENTIFIER_CHARS.sub("_", to_snake_case(name)).strip("_")
    return slug or "field"


def _multipart_file_env_var(field_key: str) -> str:
    # Nunca deriva o env var do "src" declarado na Collection (normalmente
    # um caminho local da máquina de quem criou a Collection, sem sentido
    # em outra máquina/CI) — só do nome do campo, mesmo quando a Collection
    # já trouxer um arquivo anexado ("Receber caminhos de arquivos por
    # configuração ou variável de ambiente").
    return f"{_ENV_VAR_PREFIX}UPLOAD_{_sanitize_field_identifier(field_key).upper()}"


def _multipart_file_field_preamble(field_key: str, local_name: str) -> tuple[str, ...]:
    env_var = _multipart_file_env_var(field_key)
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
        *_env_var_lookup_lines(f"{local_name}_path", env_var),
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


def _resolve_multipart_body(body: NormalizedBody) -> _BodyResolution:
    # Chegou aqui só depois de _unsupported_multipart_reason confirmar que
    # todo campo de arquivo habilitado tem "key" — reanalisar aqui é puro e
    # sem efeito colateral, mesmo padrão já usado por _resolve_auth/_resolve_body.
    preamble_lines: list[str] = []
    extra_imports: set[str] = set()
    fields_code: dict[str, str] = {}
    # Nomes de variável local já emitidos (arquivo ou campo textual
    # resolvido) — evita repetir o mesmo `os.environ.get(...)` quando o
    # mesmo campo/variável aparece mais de uma vez.
    seen_local_names: set[str] = set()

    for field in body.fields:
        if field.disabled or not field.key:
            continue

        if field.field_type == "file":
            local_name = _sanitize_field_identifier(field.key)
            if local_name not in seen_local_names:
                seen_local_names.add(local_name)
                preamble_lines.extend(_multipart_file_field_preamble(field.key, local_name))
                extra_imports |= {"os", "pytest", "mimetypes"}
            fields_code[field.key] = _render_file_payload_dict(local_name, "            ")
            continue

        # Campo textual: "Resolver variáveis em campos textuais" — só uma
        # referência pura ({{nome}}, nada mais na string) vira variável de
        # ambiente (mesmo critério conservador de _resolve_auth, via
        # _extract_pure_variable_name); qualquer outro valor (literal ou
        # com variável parcial) é embutido como texto, mesmo tratamento já
        # usado pelo body JSON (Parte 13) para conteúdo bruto.
        variable_name = _extract_pure_variable_name(field.value)
        if variable_name is None:
            fields_code[field.key] = _python_string_literal(field.value or "")
            continue

        local_name = _sanitize_field_identifier(variable_name)
        if local_name not in seen_local_names:
            seen_local_names.add(local_name)
            preamble_lines.extend(
                _env_var_lookup_lines(local_name, _to_env_var_name(variable_name))
            )
            extra_imports.add("os")
        fields_code[field.key] = local_name

    return _BodyResolution(
        preamble_lines=tuple(preamble_lines),
        multipart_fields=fields_code or None,
        extra_imports=frozenset(extra_imports),
    )


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
    method = (request.method or "get").lower()
    path = _relative_path(request.url)
    params = _build_query_params(request.url.query_parameters)
    header_resolution = _resolve_headers(
        request.headers, endpoint_source=strategy.endpoint_source, environment=environment
    )
    # Já sabido "supported" (gate em _unsupported_reason); recomputado aqui
    # (puro, sem efeito colateral) para obter o preâmbulo/headers/params
    # reais a renderizar — mesmo padrão já usado para headers/params, que
    # também são recalculados em vez de repassados da checagem de suporte.
    auth_resolution = _resolve_auth(request.auth)
    body_resolution = _resolve_body(request.body)

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

    all_imports = auth_resolution.extra_imports | body_resolution.extra_imports
    imports_block = "".join(f"import {name}\n" for name in sorted(all_imports))
    if imports_block:
        imports_block += "\n\n"

    # Ordem: preâmbulo de autenticação (configuração da requisição) antes
    # do preâmbulo de body (o que está sendo enviado) — ambos antes da
    # chamada em si.
    preamble = "".join(auth_resolution.preamble_lines) + "".join(body_resolution.preamble_lines)
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

    content = (
        f"{imports_block}"
        f"def {function_name}(api_context):\n"
        '    """\n'
        f"    Request: {safe_request_name}\n"
        f"    Method: {request.method}\n"
        f"    Endpoint: {safe_endpoint_source}\n"
        "    Scenario: success\n"
        "    Category: positive\n"
        f"    Origin: NormalizedRequest ({request.method} simples, {body_origin_note}, "
        f"{auth_origin_note}, sem variáveis não resolvidas)\n"
        '    """\n'
        "\n"
        f"{preamble}"
        f"{http_call}"
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
