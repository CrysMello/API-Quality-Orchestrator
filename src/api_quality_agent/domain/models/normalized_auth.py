from dataclasses import dataclass

from api_quality_agent.domain.models.auth_source import AuthSource
from api_quality_agent.domain.models.auth_type import AuthType
from api_quality_agent.domain.models.normalized_auth_parameter import NormalizedAuthParameter


@dataclass(frozen=True)
class NormalizedAuth:
    auth_type: AuthType
    source: AuthSource
    variable_references: tuple[str, ...]
    has_sensitive_values: bool
    raw_type: str | None
    # Parâmetros brutos do bloco de auth (Parte 12) — vazio quando
    # auth_type é NONE/INHERIT/UNKNOWN ou quando raw_auth não tinha o
    # array esperado para o tipo declarado.
    parameters: tuple[NormalizedAuthParameter, ...]
