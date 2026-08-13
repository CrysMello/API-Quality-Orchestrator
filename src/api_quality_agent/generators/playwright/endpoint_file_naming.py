import re
from collections.abc import Sequence
from dataclasses import dataclass

from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)
from api_quality_agent.shared import sanitize_filename_component

# Um parâmetro de path pode vir como :nome (Postman) ou {nome}/{{nome}}
# (OpenAPI ou variável de Collection) — ver EndpointAnalysis.source em
# domain/services/api_analysis_engine.py (_endpoint_source_label /
# _analyze_openapi_endpoint) e _PATH_VARIABLE_SEGMENT_PATTERN no mesmo
# arquivo, que já reconhece as duas formas para outro propósito (detecção
# de dependência por path).
_COLON_PARAMETER = re.compile(r"^:(.+)$")
_BRACE_PARAMETER = re.compile(r"^\{+([^{}]+)\}+$")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_INVALID_SEGMENT_CHARS = re.compile(r"[^a-z0-9_]+")

# Mesmo orçamento de tamanho usado pelos artefatos Postman (ver
# shared/filename_sanitization.py) — os arquivos Playwright vivem uma
# camada a mais de diretório (scripts/playwright/endpoints/), mas a margem
# original já é generosa o bastante para isso.
_MAX_ENDPOINT_SLUG_LENGTH = 40
_ENDPOINT_SLUG_HASH_LENGTH = 8
_FALLBACK_SLUG = "endpoint"

FILE_NAME_COLLISION_RESOLVED = "FILE_NAME_COLLISION_RESOLVED"


def to_snake_case(name: str) -> str:
    # Público: reaproveitado fora deste módulo (ex.: resolução de
    # autenticação em playwright_endpoint_test_generator.py, Parte 12, para
    # derivar nomes de variável de ambiente — apiKey -> api_key -> AQO_API_KEY).
    return _CAMEL_CASE_BOUNDARY.sub("_", name).lower()


def _sanitize_segment(segment: str) -> str:
    return _INVALID_SEGMENT_CHARS.sub("_", to_snake_case(segment)).strip("_")


def is_parameterized_segment(segment: str) -> bool:
    # Reaproveitado fora deste módulo (ex.: PlaywrightEndpointTestGenerator,
    # Parte 07) para decidir se uma URL tem parâmetro de path ainda não
    # resolvido — mesmo reconhecimento de :nome/{nome}/{{nome}} usado aqui
    # para nomear arquivos.
    return bool(_COLON_PARAMETER.match(segment) or _BRACE_PARAMETER.match(segment))


def _segment_to_slug_part(segment: str) -> str:
    colon_match = _COLON_PARAMETER.match(segment)
    if colon_match:
        return f"by_{_sanitize_segment(colon_match.group(1))}"

    brace_match = _BRACE_PARAMETER.match(segment)
    if brace_match:
        return f"by_{_sanitize_segment(brace_match.group(1))}"

    return _sanitize_segment(segment)


def endpoint_source_to_slug(endpoint_source: str) -> str:
    # Determinístico e puro: mesmo endpoint_source produz sempre o mesmo
    # slug (nenhuma aleatoriedade, nenhum estado externo). Reaproveitado
    # tanto no nome do arquivo (endpoint_source_to_file_name) quanto no
    # nome da função de teste (PlaywrightEndpointTestGenerator, Parte 07).
    method, _, raw_path = endpoint_source.strip().partition(" ")
    method_part = _sanitize_segment(method) or "unknown"

    path_parts = [
        part
        for segment in raw_path.split("/")
        if segment and (part := _segment_to_slug_part(segment))
    ]

    raw_slug = "_".join([method_part, *path_parts])
    return sanitize_filename_component(
        raw_slug,
        max_length=_MAX_ENDPOINT_SLUG_LENGTH,
        hash_length=_ENDPOINT_SLUG_HASH_LENGTH,
        fallback=_FALLBACK_SLUG,
    )


def endpoint_source_to_file_name(endpoint_source: str) -> str:
    # Não resolve colisões entre múltiplos endpoints — ver
    # resolve_endpoint_file_names.
    return f"test_{endpoint_source_to_slug(endpoint_source)}.py"


def _apply_collision_suffix(file_name: str, index: int) -> str:
    stem, _, extension = file_name.rpartition(".")
    return f"{stem}_{index:02d}.{extension}"


@dataclass(frozen=True)
class ResolvedEndpointFileNames:
    # Mesma ordem/tamanho de endpoint_sources; file_names já garantidamente
    # únicos entre si (colisões resolvidas com sufixo determinístico).
    file_names: tuple[str, ...]
    warnings: tuple[PlaywrightGenerationWarning, ...]


def resolve_endpoint_file_names(
    endpoint_sources: Sequence[str],
) -> ResolvedEndpointFileNames:
    occurrences: dict[str, int] = {}
    file_names: list[str] = []
    warnings: list[PlaywrightGenerationWarning] = []

    for endpoint_source in endpoint_sources:
        base_name = endpoint_source_to_file_name(endpoint_source)
        occurrence_index = occurrences.get(base_name, 0) + 1
        occurrences[base_name] = occurrence_index

        if occurrence_index == 1:
            file_names.append(base_name)
            continue

        resolved_name = _apply_collision_suffix(base_name, occurrence_index)
        file_names.append(resolved_name)
        warnings.append(
            PlaywrightGenerationWarning(
                code=FILE_NAME_COLLISION_RESOLVED,
                message=(
                    f"Nome de arquivo '{base_name}' já usado por outro endpoint; "
                    f"'{resolved_name}' aplicado para evitar sobrescrever o arquivo existente."
                ),
                endpoint=endpoint_source,
                scenario=None,
            )
        )

    return ResolvedEndpointFileNames(file_names=tuple(file_names), warnings=tuple(warnings))
