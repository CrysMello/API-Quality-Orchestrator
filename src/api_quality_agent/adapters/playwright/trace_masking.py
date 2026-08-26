"""P1.3 (Trace em falha) — mascaramento de um Playwright Trace (.zip) ANTES
de qualquer persistência.

Contexto de segurança (medido empiricamente contra Playwright 1.61, a
versão usada neste projeto — ver relatório da tarefa): um Trace de
`APIRequestContext` (gerado via `tracing.start(snapshots=True)` /
`tracing.stop(path=...)`) grava, em texto puro, TODA a atividade de rede:
método, URL, headers de request/response (incluindo Authorization/Cookie),
corpo do request (inline, em base64, dentro de `trace.trace`) e corpo do
request/response como arquivos de recurso separados dentro do próprio
.zip. Não existe nenhum parâmetro em `Tracing.start()`/`Tracing.stop()`
que mascare ou omita esse conteúdo — o masking é responsabilidade de quem
persiste o artefato, nunca do Playwright.

O masking já usado em HttpTransaction/AssertionResult (mask_all_occurrences
contra known_secret_values) NÃO é automaticamente suficiente aqui: ele só
apaga OCORRÊNCIAS LITERAIS de um valor já conhecido como secret. Um Trace
cru contém, adicionalmente:

  - o NOME do header (Authorization/Cookie/Set-Cookie/Proxy-Authorization)
    associado a um valor que pode nunca ter sido registrado como secret
    conhecido (ex.: um token gerado dinamicamente a cada execução, nunca
    presente numa variável de Environment marcada "secret") — por isso
    este módulo redige esses headers/cookies POR NOME, sempre, além do
    masking por valor conhecido;
  - o corpo do request duplicado como uma string BASE64 dentro de
    `trace.trace` — masking de substring não alcança um valor codificado
    em base64, então este módulo decodifica, mascara o texto decodificado
    e recodifica;
  - arquivos de recurso (request/response body) que podem não ser texto
    UTF-8 (ex.: binário genuíno) — masking de substring é impossível de
    verificar sobre bytes arbitrários sem risco de falso negativo. Este
    módulo NUNCA finge ter mascarado um recurso assim: substitui o
    conteúdo inteiro por um aviso fixo, preferindo remover a informação a
    entregar uma falsa sensação de segurança.

Limitação conhecida e documentada (não escondida): valores sensíveis que
não estão em `known_secret_values` E não correspondem a um header/cookie
de nome conhecido (ex.: um campo de negócio comum, tipo "password", num
corpo JSON, quando o valor em si não é um secret cadastrado) NÃO são
mascarados — exatamente a mesma limitação já aceita hoje para
HttpTransaction/AssertionResult, não uma regressão introduzida aqui.
"""

import base64
import json
import re
import zipfile
from pathlib import Path

from api_quality_agent.shared import mask_all_occurrences

# Membros de texto/JSON-lines que o Playwright sempre grava num trace de
# APIRequestContext com snapshots=True/sources=False (nomes fixos do
# próprio formato do Playwright — nunca inventados aqui). Qualquer outro
# membro do .zip (sempre dentro de "resources/") é tratado como um
# recurso binário-ou-texto genérico (ver _mask_resource_member).
_TEXT_TRACE_MEMBERS = frozenset({"trace.trace", "trace.network", "trace.stacks"})

# Nomes de header/cookie estruturalmente sensíveis — redigidos por NOME,
# sempre, independente de estarem ou não em known_secret_values (ver
# docstring do módulo). Comparação sempre case-insensitive (HTTP não
# distingue caixa em nomes de header).
_SENSITIVE_HEADER_NAMES = frozenset(
    {"authorization", "cookie", "set-cookie", "proxy-authorization"}
)
_REDACTED_VALUE = "[REDACTED]"
_STRIPPED_BINARY_PLACEHOLDER = (
    b"<binary resource omitted from trace: could not verify absence of secrets>"
)

# trace.trace também registra o MESMO header sensível de novo, em texto
# livre, dentro de eventos "log" (ex.: {"type":"log","message":"  Cookie:
# session=abc123secret"}) — a redação estrutural do array "headers" (ver
# _redact_sensitive_structures) não alcança essa duplicata textual, então
# esta é uma segunda regra, também baseada em NOME (nunca em valor
# adivinhado): qualquer "message" que comece com um desses nomes de
# header tem o valor após ":" substituído, preservando o nome do header e
# a indentação original.
_LOG_HEADER_LINE_PATTERN = re.compile(
    r"^(\s*(?:authorization|cookie|set-cookie|proxy-authorization)\s*:\s*).*$",
    re.IGNORECASE,
)


def mask_trace_archive(
    *, source_path: Path, destination_path: Path, known_secret_values: tuple[str, ...]
) -> None:
    # Nunca modifica o .zip de origem: lê tudo, escreve um .zip NOVO em
    # destination_path — o chamador decide o que fazer com o original
    # (PlaywrightAdapter descarta o diretório de trabalho bruto inteiro
    # depois de chamar esta função).
    with (
        zipfile.ZipFile(source_path, "r") as source_zip,
        zipfile.ZipFile(destination_path, "w", zipfile.ZIP_DEFLATED) as dest_zip,
    ):
        for info in source_zip.infolist():
            raw_bytes = source_zip.read(info.filename)
            if info.filename in _TEXT_TRACE_MEMBERS:
                masked_bytes = _mask_trace_text_member(raw_bytes, known_secret_values)
            else:
                masked_bytes = _mask_resource_member(raw_bytes, known_secret_values)
            dest_zip.writestr(info.filename, masked_bytes)


def _mask_trace_text_member(raw_bytes: bytes, known_secret_values: tuple[str, ...]) -> bytes:
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Nunca esperado para trace.trace/trace.network/trace.stacks (são
        # sempre texto pelo próprio formato do Playwright) — mas nunca
        # persiste algo não verificável em vez de assumir que "deve estar
        # ok".
        return _STRIPPED_BINARY_PLACEHOLDER
    masked_lines = [_mask_trace_line(line, known_secret_values) for line in text.split("\n")]
    return "\n".join(masked_lines).encode("utf-8")


def _mask_trace_line(line: str, known_secret_values: tuple[str, ...]) -> str:
    # trace.trace/trace.network são NDJSON (um objeto por linha) — linhas
    # vazias (ex.: EOF) passam direto.
    if not line:
        return line
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        # Formato inesperado para esta linha — nunca falha a exportação
        # inteira por causa disso, só aplica o masking genérico por valor
        # conhecido (mesmo piso de segurança de qualquer outra evidência
        # deste projeto).
        return mask_all_occurrences(line, known_secret_values)

    _redact_sensitive_structures(payload, known_secret_values)
    serialized = json.dumps(payload, ensure_ascii=False)
    # Defesa em profundidade: depois da redação estrutural (por nome de
    # header/cookie) e da decodificação/mascaramento de postData em
    # base64, ainda roda o masking genérico por valor conhecido sobre a
    # linha inteira — cobre mensagens de log em texto livre (ex.:
    # "Authorization: Bearer ...") que a redação estrutural não alcança.
    return mask_all_occurrences(serialized, known_secret_values)


def _redact_sensitive_structures(node: object, known_secret_values: tuple[str, ...]) -> None:
    if isinstance(node, dict):
        headers = node.get("headers")
        if isinstance(headers, list):
            for header in headers:
                if not isinstance(header, dict):
                    continue
                name = str(header.get("name", "")).lower()
                if name in _SENSITIVE_HEADER_NAMES:
                    header["value"] = _REDACTED_VALUE

        cookies = node.get("cookies")
        if isinstance(cookies, list):
            for cookie in cookies:
                if isinstance(cookie, dict) and "value" in cookie:
                    cookie["value"] = _REDACTED_VALUE

        post_data = node.get("postData")
        if isinstance(post_data, str) and post_data:
            node["postData"] = _mask_base64_payload(post_data, known_secret_values)

        message = node.get("message")
        if isinstance(message, str):
            node["message"] = _LOG_HEADER_LINE_PATTERN.sub(
                lambda match: match.group(1) + _REDACTED_VALUE, message
            )

        for value in node.values():
            _redact_sensitive_structures(value, known_secret_values)
    elif isinstance(node, list):
        for item in node:
            _redact_sensitive_structures(item, known_secret_values)


def _mask_base64_payload(value: str, known_secret_values: tuple[str, ...]) -> str:
    # `params.postData` (trace.trace, evento "before" de uma chamada de
    # APIRequestContext) é sempre uma string base64 do corpo enviado —
    # masking de substring nunca alcançaria um valor codificado assim.
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception:  # noqa: BLE001 - base64 inválido nunca deve derrubar a exportação
        return value
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        # Corpo binário genuíno (ex.: upload de arquivo) — mesma política
        # de _mask_resource_member: nunca persiste algo não verificável.
        return base64.b64encode(_STRIPPED_BINARY_PLACEHOLDER).decode("ascii")
    masked = mask_all_occurrences(text, known_secret_values)
    return base64.b64encode(masked.encode("utf-8")).decode("ascii")


def _mask_resource_member(raw_bytes: bytes, known_secret_values: tuple[str, ...]) -> bytes:
    # Arquivos em "resources/" são o corpo real de um request/response
    # (ver trace.network -> postData._sha1 / response.content._sha1) —
    # quase sempre texto (JSON) nas suítes geradas por este projeto, mas
    # nunca assumido: se não decodificar como UTF-8, é substituído por um
    # aviso fixo em vez de persistido cru (nunca uma falsa sensação de
    # segurança sobre bytes não verificáveis).
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return _STRIPPED_BINARY_PLACEHOLDER
    return mask_all_occurrences(text, known_secret_values).encode("utf-8")
