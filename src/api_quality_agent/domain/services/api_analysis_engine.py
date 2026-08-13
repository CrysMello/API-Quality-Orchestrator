import re

from api_quality_agent.domain.models import (
    AnalysisWarning,
    AnalyzedCollectionRequest,
    ApiAnalysisResult,
    ApiSpecification,
    AuthType,
    CollectionFolder,
    CollectionRequest,
    DependencyCandidate,
    DependencyConfidence,
    DependencyEvidenceType,
    Endpoint,
    EndpointAnalysis,
    NormalizedRequest,
    ParameterAnalysis,
    PostmanCollectionDocument,
    SecurityDefinition,
    UnknownCollectionItem,
)
from api_quality_agent.domain.services.postman_request_normalizer import PostmanRequestNormalizer

_OPENAPI_PATH_VARIABLE_PATTERN = re.compile(r"\{([^{}]+)\}")
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_SET_VARIABLE_PATTERN = re.compile(
    r"pm\.(?:environment|collectionVariables|variables|globals)\.set\(\s*[\"']([^\"']+)[\"']"
)
_PATH_VARIABLE_SEGMENT_PATTERN = re.compile(r"^(\{.*\}|:.+)$")
_CREATION_METHODS = frozenset({"POST"})
_MUTATING_METHODS_FOR_TARGET = frozenset({"GET", "PUT", "PATCH", "DELETE"})


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


class ApiAnalysisEngine:
    def __init__(self, normalizer: PostmanRequestNormalizer | None = None) -> None:
        self._normalizer = normalizer or PostmanRequestNormalizer()

    def analyze(
        self, source: ApiSpecification | PostmanCollectionDocument
    ) -> ApiAnalysisResult:
        if isinstance(source, ApiSpecification):
            return self.analyze_specification(source)
        if isinstance(source, PostmanCollectionDocument):
            return self.analyze_collection(source)
        raise TypeError(f"Tipo de origem não suportado: {type(source).__name__}")

    def analyze_specification(self, specification: ApiSpecification) -> ApiAnalysisResult:
        warnings: list[AnalysisWarning] = []
        endpoints = tuple(
            _analyze_openapi_endpoint(endpoint, specification.security_schemes, warnings)
            for endpoint in specification.endpoints
        )
        dependencies = _find_path_dependencies(endpoints)

        return ApiAnalysisResult(
            source_type=specification.spec_type.value,
            endpoints=endpoints,
            dependencies=dependencies,
            warnings=tuple(warnings),
        )

    def analyze_collection(self, document: PostmanCollectionDocument) -> ApiAnalysisResult:
        warnings, entries, endpoints = self._analyze_collection_entries(document)

        dependencies = _find_variable_dependencies(entries, endpoints)
        dependencies += _find_path_dependencies(endpoints)

        return ApiAnalysisResult(
            source_type="postman",
            endpoints=endpoints,
            dependencies=dependencies,
            warnings=tuple(warnings),
        )

    def analyze_collection_requests(
        self, document: PostmanCollectionDocument
    ) -> tuple[AnalyzedCollectionRequest, ...]:
        # Mesma travessia e mesma ordem usadas por analyze_collection: permite
        # relacionar cada EndpointAnalysis ao CollectionRequest bruto que o
        # originou (necessário para acessar examples/scripts na geração), e
        # repassa o NormalizedRequest já calculado (nunca normaliza de novo).
        _warnings, entries, endpoints = self._analyze_collection_entries(document)
        return tuple(
            AnalyzedCollectionRequest(raw_request=raw, normalized_request=normalized, analysis=analysis)
            for (raw, normalized), analysis in zip(entries, endpoints)
        )

    def _analyze_collection_entries(
        self, document: PostmanCollectionDocument
    ) -> tuple[
        list[AnalysisWarning],
        list[tuple[CollectionRequest, NormalizedRequest]],
        tuple[EndpointAnalysis, ...],
    ]:
        warnings: list[AnalysisWarning] = [
            AnalysisWarning(code="COLLECTION_PARSER_WARNING", message=message, endpoint=None)
            for message in document.warnings
        ]

        entries: list[tuple[CollectionRequest, NormalizedRequest]] = []
        _collect_requests(document.items, entries, warnings, self._normalizer)

        endpoints = tuple(
            _analyze_postman_endpoint(raw, normalized, warnings)
            for raw, normalized in entries
        )

        return warnings, entries, endpoints


# --- OpenAPI / Swagger -------------------------------------------------------


def _resolve_openapi_auth_type(
    endpoint: Endpoint, security_schemes: tuple[SecurityDefinition, ...]
) -> str | None:
    if not endpoint.security_requirement_names:
        return None
    schemes_by_name = {scheme.name: scheme for scheme in security_schemes}
    scheme = schemes_by_name.get(endpoint.security_requirement_names[0])
    return scheme.type if scheme is not None else None


def _analyze_openapi_endpoint(
    endpoint: Endpoint,
    security_schemes: tuple[SecurityDefinition, ...],
    warnings: list[AnalysisWarning],
) -> EndpointAnalysis:
    source = f"{endpoint.method} {endpoint.path}"

    parameters = tuple(
        ParameterAnalysis(name=p.name, location=p.location.value, required=p.required)
        for p in endpoint.parameters
    )
    for parameter in endpoint.parameters:
        if parameter.schema is None:
            warnings.append(
                AnalysisWarning(
                    code="MISSING_PARAMETER_SCHEMA",
                    message=f"Parâmetro '{parameter.name}' sem schema declarado.",
                    endpoint=source,
                )
            )

    has_request_body = endpoint.request is not None
    request_content_types = (
        tuple(mt.content_type for mt in endpoint.request.media_types) if endpoint.request else ()
    )
    if endpoint.request is not None:
        if not endpoint.request.media_types:
            warnings.append(
                AnalysisWarning(
                    code="MISSING_REQUEST_SCHEMA",
                    message="Request body sem nenhum content type declarado.",
                    endpoint=source,
                )
            )
        for media_type in endpoint.request.media_types:
            if media_type.schema is None:
                warnings.append(
                    AnalysisWarning(
                        code="MISSING_REQUEST_SCHEMA",
                        message=f"Request body '{media_type.content_type}' sem schema declarado.",
                        endpoint=source,
                    )
                )

    response_status_codes = tuple(response.status_code for response in endpoint.responses)
    if not response_status_codes:
        warnings.append(
            AnalysisWarning(
                code="NO_RESPONSES_DOCUMENTED",
                message="Nenhuma response documentada para este endpoint.",
                endpoint=source,
            )
        )
    response_content_types = _dedupe(
        tuple(
            media_type.content_type
            for response in endpoint.responses
            for media_type in response.media_types
        )
    )

    auth_type = _resolve_openapi_auth_type(endpoint, security_schemes)
    variables_used = tuple(_OPENAPI_PATH_VARIABLE_PATTERN.findall(endpoint.path))

    example_count = sum(
        1 for mt in (endpoint.request.media_types if endpoint.request else ()) if mt.example is not None
    ) + sum(
        1
        for response in endpoint.responses
        for mt in response.media_types
        if mt.example is not None
    )
    has_examples = example_count > 0
    if not has_examples:
        warnings.append(
            AnalysisWarning(
                code="NO_EXAMPLES_AVAILABLE",
                message="Nenhum example disponível para este endpoint.",
                endpoint=source,
            )
        )

    return EndpointAnalysis(
        source=source,
        method=endpoint.method,
        path=endpoint.path,
        operation_id=endpoint.operation_id,
        parameters=parameters,
        has_request_body=has_request_body,
        request_content_types=request_content_types,
        response_status_codes=response_status_codes,
        response_content_types=response_content_types,
        auth_type=auth_type,
        variables_used=variables_used,
        has_examples=has_examples,
        example_count=example_count,
    )


# --- Postman Collection -------------------------------------------------------


def _collect_requests(
    items: tuple,
    output: list[tuple[CollectionRequest, NormalizedRequest]],
    warnings: list[AnalysisWarning],
    normalizer: PostmanRequestNormalizer,
) -> None:
    for item in items:
        if isinstance(item, CollectionFolder):
            _collect_requests(item.items, output, warnings, normalizer)
        elif isinstance(item, CollectionRequest):
            output.append((item, normalizer.normalize(item)))
        elif isinstance(item, UnknownCollectionItem):
            warnings.append(
                AnalysisWarning(
                    code="UNKNOWN_ITEM_SKIPPED",
                    message=f"Item desconhecido ignorado na análise: {item.name!r}",
                    endpoint=None,
                )
            )


def _endpoint_source_label(raw: CollectionRequest, normalized: NormalizedRequest) -> str:
    method = normalized.method or "?"
    if normalized.url.path:
        return f"{method} /{'/'.join(normalized.url.path)}"
    if normalized.url.raw:
        return f"{method} {normalized.url.raw}"
    return f"{method} {raw.name or raw.item_id or '<sem nome>'}"


def _extract_content_type_from_example_headers(headers: tuple) -> str | None:
    for header in headers:
        if not isinstance(header, dict):
            continue
        key = header.get("key")
        if isinstance(key, str) and key.lower() == "content-type":
            value = header.get("value")
            return value if isinstance(value, str) else None
    return None


def _extract_defined_variables(raw: CollectionRequest) -> tuple[str, ...]:
    names: list[str] = []
    for event in raw.events:
        if event.listen != "test":
            continue
        for line in event.exec_lines:
            names.extend(_SET_VARIABLE_PATTERN.findall(line))
    return _dedupe(tuple(names))


def _analyze_postman_endpoint(
    raw: CollectionRequest,
    normalized: NormalizedRequest,
    warnings: list[AnalysisWarning],
) -> EndpointAnalysis:
    source = _endpoint_source_label(raw, normalized)
    path = "/" + "/".join(normalized.url.path) if normalized.url.path else None

    parameters = tuple(
        ParameterAnalysis(name=variable.key, location="path", required=True)
        for variable in normalized.url.variables
        if variable.key
    ) + tuple(
        ParameterAnalysis(name=query.key, location="query", required=not query.disabled)
        for query in normalized.url.query_parameters
        if query.key
    )

    has_request_body = normalized.body.has_content
    request_content_types = (
        (normalized.body.content_type,) if normalized.body.content_type else ()
    )

    response_status_codes = tuple(
        str(example.code) for example in raw.examples if example.code is not None
    )
    response_content_types = _dedupe(
        tuple(
            content_type
            for example in raw.examples
            if (content_type := _extract_content_type_from_example_headers(example.headers))
        )
    )

    auth_type = (
        normalized.auth.auth_type.value
        if normalized.auth.auth_type not in (AuthType.NONE, AuthType.INHERIT, AuthType.UNKNOWN)
        else None
    )

    variables_used = _dedupe(
        tuple(variable.key for variable in normalized.url.variables if variable.key)
        + tuple(_VARIABLE_PATTERN.findall(normalized.url.raw or ""))
        + normalized.auth.variable_references
        + normalized.body.variable_references
    )

    example_count = len(raw.examples)
    has_examples = example_count > 0
    if not has_examples:
        warnings.append(
            AnalysisWarning(
                code="NO_EXAMPLES_AVAILABLE",
                message="Nenhum example salvo para esta request.",
                endpoint=source,
            )
        )

    return EndpointAnalysis(
        source=source,
        method=normalized.method,
        path=path,
        operation_id=None,
        parameters=parameters,
        has_request_body=has_request_body,
        request_content_types=request_content_types,
        response_status_codes=response_status_codes,
        response_content_types=response_content_types,
        auth_type=auth_type,
        variables_used=variables_used,
        has_examples=has_examples,
        example_count=example_count,
    )


def _find_variable_dependencies(
    entries: list[tuple[CollectionRequest, NormalizedRequest]],
    endpoints: tuple[EndpointAnalysis, ...],
) -> tuple[DependencyCandidate, ...]:
    definers: dict[str, str] = {}
    for (raw, _normalized), analysis in zip(entries, endpoints):
        for variable_name in _extract_defined_variables(raw):
            definers.setdefault(variable_name, analysis.source)

    dependencies: list[DependencyCandidate] = []
    for analysis in endpoints:
        for variable_name in analysis.variables_used:
            definer_source = definers.get(variable_name)
            if definer_source is None or definer_source == analysis.source:
                continue
            dependencies.append(
                DependencyCandidate(
                    source_endpoint=definer_source,
                    target_endpoint=analysis.source,
                    confidence=DependencyConfidence.CONFIRMED,
                    evidence_type=DependencyEvidenceType.VARIABLE_REFERENCE,
                    description=(
                        f"'{analysis.source}' usa a variável '{{{{{variable_name}}}}}' "
                        f"definida por um script de teste em '{definer_source}'."
                    ),
                )
            )
    return tuple(dependencies)


# --- Dependência estrutural por path (comum a OpenAPI e Postman) ------------


def _path_segments(path: str) -> tuple[str, ...]:
    return tuple(segment for segment in path.split("/") if segment)


def _is_path_variable_segment(segment: str) -> bool:
    return bool(_PATH_VARIABLE_SEGMENT_PATTERN.match(segment)) or _VARIABLE_PATTERN.fullmatch(
        segment
    ) is not None


def _find_path_dependencies(
    endpoints: tuple[EndpointAnalysis, ...],
) -> tuple[DependencyCandidate, ...]:
    dependencies: list[DependencyCandidate] = []
    for source_endpoint in endpoints:
        if source_endpoint.path is None or source_endpoint.method is None:
            continue
        source_segments = _path_segments(source_endpoint.path)

        for target_endpoint in endpoints:
            if target_endpoint is source_endpoint:
                continue
            if target_endpoint.path is None or target_endpoint.method is None:
                continue
            target_segments = _path_segments(target_endpoint.path)

            if len(target_segments) != len(source_segments) + 1:
                continue
            if target_segments[: len(source_segments)] != source_segments:
                continue
            if not _is_path_variable_segment(target_segments[-1]):
                continue

            if (
                source_endpoint.method in _CREATION_METHODS
                and target_endpoint.method in _MUTATING_METHODS_FOR_TARGET
            ):
                confidence = DependencyConfidence.CONFIRMED
            else:
                confidence = DependencyConfidence.SUGGESTED

            dependencies.append(
                DependencyCandidate(
                    source_endpoint=source_endpoint.source,
                    target_endpoint=target_endpoint.source,
                    confidence=confidence,
                    evidence_type=DependencyEvidenceType.PATH_CORRESPONDENCE,
                    description=(
                        f"'{target_endpoint.source}' opera sobre um item específico do recurso "
                        f"exposto por '{source_endpoint.source}'."
                    ),
                )
            )
    return tuple(dependencies)
