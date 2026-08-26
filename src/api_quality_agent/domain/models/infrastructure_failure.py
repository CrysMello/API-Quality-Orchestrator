from dataclasses import dataclass

from api_quality_agent.domain.models.infrastructure_failure_type import InfrastructureFailureType


@dataclass(frozen=True)
class InfrastructureFailure:
    failure_type: InfrastructureFailureType
    message: str
    # P1.5 (infrastructure failure das evidências): source/test_id só são
    # preenchidos quando a falha é de uma EVIDÊNCIA específica (ex.: um
    # Trace que não pôde ser mascarado/movido/persistido para um teste
    # específico) — nunca para as falhas de infraestrutura "de execução
    # inteira" já existentes (executável não encontrado, timeout, etc.),
    # que continuam com os dois em None, exatamente como antes. Nunca
    # inventa um test_id: None quando a falha ocorre antes de existir um
    # (ver PlaywrightAdapter/PersistExecutionResultUseCase).
    source: str | None = None
    test_id: str | None = None
