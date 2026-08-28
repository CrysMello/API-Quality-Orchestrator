from dataclasses import dataclass

from api_quality_agent.domain.models.parameter_analysis import ParameterAnalysis


@dataclass(frozen=True)
class EndpointAnalysis:
    source: str
    method: str | None
    path: str | None
    operation_id: str | None
    parameters: tuple[ParameterAnalysis, ...]
    has_request_body: bool
    request_content_types: tuple[str, ...]
    response_status_codes: tuple[str, ...]
    response_content_types: tuple[str, ...]
    auth_type: str | None
    variables_used: tuple[str, ...]
    has_examples: bool
    example_count: int
    # P3.3 — nomes de variável definidos por um script de teste REAL da
    # própria Collection (ex.: pm.collectionVariables.set("customer_id",
    # ...)), já detectados por api_analysis_engine._extract_defined_
    # variables para outro propósito (DependencyCandidate) — aditivo
    # (default preserva toda construção existente, inclusive o caminho
    # OpenAPI, que nunca tem scripts de teste e nunca preenche isto).
    # Consumido por TestStrategyEngine como a ÚNICA fonte aceita de nome
    # semântico explícito para uma VariableExtraction cujo campo de
    # resposta é genérico (ex.: "id") — nunca um matching heurístico entre
    # nomes.
    variables_defined: tuple[str, ...] = ()
