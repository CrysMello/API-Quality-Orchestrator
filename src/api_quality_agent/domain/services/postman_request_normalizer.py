import re
import urllib.parse
from collections.abc import Mapping
from typing import Any

from api_quality_agent.domain.models import (
    AuthSource,
    AuthType,
    BodyMode,
    CollectionRequest,
    NormalizationContext,
    NormalizationWarning,
    NormalizedAuth,
    NormalizedAuthParameter,
    NormalizedBody,
    NormalizedBodyField,
    NormalizedHeader,
    NormalizedQueryParameter,
    NormalizedRequest,
    NormalizedUrl,
    NormalizedUrlVariable,
)

_VARIABLE_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

_AUTH_TYPE_MAP: dict[str, AuthType] = {
    "noauth": AuthType.NONE,
    "bearer": AuthType.BEARER,
    "apikey": AuthType.API_KEY,
    "basic": AuthType.BASIC,
    "oauth2": AuthType.OAUTH2,
    "digest": AuthType.DIGEST,
    "awsv4": AuthType.AWS_V4,
    "hawk": AuthType.HAWK,
    "ntlm": AuthType.NTLM,
    "edgegrid": AuthType.EDGEGRID,
}

_BODY_MODE_MAP: dict[str, BodyMode] = {
    "raw": BodyMode.RAW,
    "formdata": BodyMode.FORMDATA,
    "urlencoded": BodyMode.URLENCODED,
    "graphql": BodyMode.GRAPHQL,
    "file": BodyMode.FILE,
}


class PostmanRequestNormalizer:
    def normalize(
        self,
        request: CollectionRequest,
        context: NormalizationContext | None = None,
    ) -> NormalizedRequest:
        warnings: list[NormalizationWarning] = []

        headers = _normalize_headers(request.headers)
        url = _normalize_url(request.url, request_id=request.item_id, warnings=warnings)
        auth = _normalize_auth(
            request.auth, request_id=request.item_id, context=context, warnings=warnings
        )
        body = _normalize_body(
            request.body, headers, request_id=request.item_id, warnings=warnings
        )

        return NormalizedRequest(
            request_id=request.item_id,
            name=request.name,
            method=request.method,
            url=url,
            auth=auth,
            body=body,
            headers=headers,
            warnings=tuple(warnings),
        )


def _find_variable_references(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(_VARIABLE_PATTERN.findall(value))


def _is_pure_variable_reference(value: str) -> bool:
    return _VARIABLE_PATTERN.fullmatch(value.strip()) is not None


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _normalize_headers(raw_headers: tuple[Mapping[str, Any], ...]) -> tuple[NormalizedHeader, ...]:
    return tuple(
        NormalizedHeader(
            key=header.get("key") if isinstance(header.get("key"), str) else None,
            value=header.get("value") if isinstance(header.get("value"), str) else None,
            disabled=bool(header.get("disabled", False)),
        )
        for header in raw_headers
    )


def _extract_content_type_header(headers: tuple[NormalizedHeader, ...]) -> str | None:
    for header in headers:
        if isinstance(header.key, str) and header.key.lower() == "content-type":
            return header.value
    return None


# --- URL -------------------------------------------------------------------


def _extract_string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    if isinstance(value, str):
        return (value,)
    return ()


def _normalize_url(
    raw_url: Mapping[str, Any] | str | None,
    *,
    request_id: str | None,
    warnings: list[NormalizationWarning],
) -> NormalizedUrl:
    if raw_url is None:
        return NormalizedUrl(raw=None, protocol=None, host=(), path=(), query_parameters=(), variables=())

    if isinstance(raw_url, str):
        return _normalize_url_from_string(raw_url)

    if isinstance(raw_url, Mapping):
        return _normalize_url_from_object(raw_url)

    warnings.append(
        NormalizationWarning(
            code="UNSUPPORTED_URL_SHAPE",
            message=f"Formato de URL não suportado: {type(raw_url).__name__}",
            field="url",
            request_id=request_id,
        )
    )
    return NormalizedUrl(raw=None, protocol=None, host=(), path=(), query_parameters=(), variables=())


def _normalize_url_from_string(raw: str) -> NormalizedUrl:
    parsed = urllib.parse.urlsplit(raw)
    protocol = parsed.scheme or None
    host = tuple(parsed.netloc.split(".")) if parsed.netloc else ()
    path = tuple(segment for segment in parsed.path.split("/") if segment)
    query_parameters = tuple(
        NormalizedQueryParameter(key=key, value=value, disabled=False)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    )
    return NormalizedUrl(
        raw=raw,
        protocol=protocol,
        host=host,
        path=path,
        query_parameters=query_parameters,
        variables=(),
    )


def _normalize_url_from_object(raw: Mapping[str, Any]) -> NormalizedUrl:
    raw_value = raw.get("raw")
    raw_str = raw_value if isinstance(raw_value, str) else None

    protocol = raw.get("protocol")
    protocol = protocol if isinstance(protocol, str) else None

    host = _extract_string_tuple(raw.get("host"))
    path = _extract_string_tuple(raw.get("path"))

    query_parameters = tuple(
        NormalizedQueryParameter(
            key=entry.get("key") if isinstance(entry.get("key"), str) else None,
            value=entry.get("value") if isinstance(entry.get("value"), str) else None,
            disabled=bool(entry.get("disabled", False)),
        )
        for entry in (raw.get("query") or [])
        if isinstance(entry, Mapping)
    )

    variables = tuple(
        NormalizedUrlVariable(
            key=entry.get("key") if isinstance(entry.get("key"), str) else None,
            value=entry.get("value") if isinstance(entry.get("value"), str) else None,
        )
        for entry in (raw.get("variable") or [])
        if isinstance(entry, Mapping)
    )

    return NormalizedUrl(
        raw=raw_str,
        protocol=protocol,
        host=host,
        path=path,
        query_parameters=query_parameters,
        variables=variables,
    )


# --- Auth --------------------------------------------------------------------


def _extract_auth_entries(raw_auth: Mapping[str, Any], raw_type: str) -> list[Mapping[str, Any]]:
    entries = raw_auth.get(raw_type)
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, Mapping)]


def _extract_auth_variable_references(raw_auth: Mapping[str, Any], raw_type: str) -> tuple[str, ...]:
    refs: list[str] = []
    for entry in _extract_auth_entries(raw_auth, raw_type):
        value = entry.get("value")
        if isinstance(value, str):
            refs.extend(_find_variable_references(value))
    return _dedupe(tuple(refs))


def _auth_has_sensitive_values(raw_auth: Mapping[str, Any], raw_type: str) -> bool:
    for entry in _extract_auth_entries(raw_auth, raw_type):
        value = entry.get("value")
        if isinstance(value, str) and value and not _is_pure_variable_reference(value):
            return True
    return False


def _normalize_auth_parameters(
    raw_auth: Mapping[str, Any], raw_type: str
) -> tuple[NormalizedAuthParameter, ...]:
    return tuple(
        NormalizedAuthParameter(
            key=entry.get("key") if isinstance(entry.get("key"), str) else None,
            value=entry.get("value") if isinstance(entry.get("value"), str) else None,
        )
        for entry in _extract_auth_entries(raw_auth, raw_type)
    )


def _normalize_auth(
    raw_auth: Mapping[str, Any] | None,
    *,
    request_id: str | None,
    context: NormalizationContext | None,
    warnings: list[NormalizationWarning],
) -> NormalizedAuth:
    if raw_auth is None:
        parent_has_auth = context.parent_has_explicit_auth if context is not None else None
        if parent_has_auth is False:
            return NormalizedAuth(
                auth_type=AuthType.NONE,
                source=AuthSource.NONE,
                variable_references=(),
                has_sensitive_values=False,
                raw_type=None,
                parameters=(),
            )
        if parent_has_auth is None:
            warnings.append(
                NormalizationWarning(
                    code="AUTH_SOURCE_UNRESOLVED",
                    message="Não foi possível determinar se há autenticação herdada de um ancestral.",
                    field="auth",
                    request_id=request_id,
                )
            )
        return NormalizedAuth(
            auth_type=AuthType.INHERIT,
            source=AuthSource.INHERITED,
            variable_references=(),
            has_sensitive_values=False,
            raw_type=None,
            parameters=(),
        )

    raw_type = raw_auth.get("type")
    raw_type = raw_type if isinstance(raw_type, str) else None

    if raw_type == "noauth":
        return NormalizedAuth(
            auth_type=AuthType.NONE,
            source=AuthSource.NONE,
            variable_references=(),
            has_sensitive_values=False,
            raw_type=raw_type,
            parameters=(),
        )

    auth_type = _AUTH_TYPE_MAP.get(raw_type, AuthType.UNKNOWN) if raw_type else AuthType.UNKNOWN
    if auth_type is AuthType.UNKNOWN or raw_type is None:
        warnings.append(
            NormalizationWarning(
                code="UNKNOWN_AUTH_TYPE",
                message=f"Tipo de autenticação não reconhecido: {raw_type!r}",
                field="auth",
                request_id=request_id,
            )
        )
        return NormalizedAuth(
            auth_type=AuthType.UNKNOWN,
            source=AuthSource.REQUEST,
            variable_references=(),
            has_sensitive_values=False,
            raw_type=raw_type,
            parameters=(),
        )

    return NormalizedAuth(
        auth_type=auth_type,
        source=AuthSource.REQUEST,
        variable_references=_extract_auth_variable_references(raw_auth, raw_type),
        has_sensitive_values=_auth_has_sensitive_values(raw_auth, raw_type),
        raw_type=raw_type,
        parameters=_normalize_auth_parameters(raw_auth, raw_type),
    )


# --- Body --------------------------------------------------------------------


def _normalize_body_fields(entries: Any) -> tuple[NormalizedBodyField, ...]:
    if not isinstance(entries, list):
        return ()
    return tuple(
        NormalizedBodyField(
            key=entry.get("key") if isinstance(entry.get("key"), str) else None,
            value=entry.get("value") if isinstance(entry.get("value"), str) else None,
            field_type=entry.get("type") if isinstance(entry.get("type"), str) else None,
            disabled=bool(entry.get("disabled", False)),
        )
        for entry in entries
        if isinstance(entry, Mapping)
    )


def _empty_body(mode: BodyMode, content_type: str | None) -> NormalizedBody:
    return NormalizedBody(
        mode=mode,
        content_type=content_type,
        has_content=False,
        text_content=None,
        fields=(),
        graphql_query=None,
        variable_references=(),
    )


def _normalize_body(
    raw_body: Mapping[str, Any] | None,
    headers: tuple[NormalizedHeader, ...],
    *,
    request_id: str | None,
    warnings: list[NormalizationWarning],
) -> NormalizedBody:
    content_type = _extract_content_type_header(headers)

    if raw_body is None:
        return _empty_body(BodyMode.NONE, content_type)

    raw_mode = raw_body.get("mode")
    raw_mode = raw_mode if isinstance(raw_mode, str) else None

    if raw_mode is None:
        return _empty_body(BodyMode.NONE, content_type)

    mode = _BODY_MODE_MAP.get(raw_mode, BodyMode.UNKNOWN)
    if mode is BodyMode.UNKNOWN:
        warnings.append(
            NormalizationWarning(
                code="UNKNOWN_BODY_MODE",
                message=f"Modo de body não reconhecido: {raw_mode!r}",
                field="body",
                request_id=request_id,
            )
        )
        return _empty_body(BodyMode.UNKNOWN, content_type)

    if mode is BodyMode.RAW:
        raw_text = raw_body.get("raw")
        text_content = raw_text if isinstance(raw_text, str) else None
        return NormalizedBody(
            mode=mode,
            content_type=content_type,
            has_content=bool(text_content),
            text_content=text_content,
            fields=(),
            graphql_query=None,
            variable_references=_find_variable_references(text_content),
        )

    if mode in (BodyMode.FORMDATA, BodyMode.URLENCODED):
        fields = _normalize_body_fields(raw_body.get(raw_mode))
        variable_refs = _dedupe(
            tuple(ref for field in fields for ref in _find_variable_references(field.value))
        )
        return NormalizedBody(
            mode=mode,
            content_type=content_type,
            has_content=len(fields) > 0,
            text_content=None,
            fields=fields,
            graphql_query=None,
            variable_references=variable_refs,
        )

    if mode is BodyMode.GRAPHQL:
        graphql = raw_body.get("graphql")
        query = graphql.get("query") if isinstance(graphql, Mapping) else None
        query = query if isinstance(query, str) else None
        return NormalizedBody(
            mode=mode,
            content_type=content_type,
            has_content=bool(query),
            text_content=None,
            fields=(),
            graphql_query=query,
            variable_references=_find_variable_references(query),
        )

    # mode is BodyMode.FILE
    file_info = raw_body.get("file")
    file_src = file_info.get("src") if isinstance(file_info, Mapping) else None
    file_src = file_src if isinstance(file_src, str) else None
    fields = (
        (NormalizedBodyField(key="src", value=file_src, field_type="file", disabled=False),)
        if file_src
        else ()
    )
    return NormalizedBody(
        mode=mode,
        content_type=content_type,
        has_content=bool(file_src),
        text_content=None,
        fields=fields,
        graphql_query=None,
        variable_references=(),
    )
