from dataclasses import dataclass

from api_quality_agent.domain.models.infrastructure_failure import InfrastructureFailure
from api_quality_agent.domain.models.test_failure import TestFailure


@dataclass(frozen=True)
class ExecutionResult:
    collection_source: str
    success: bool
    exit_code: int | None
    duration_seconds: float
    total_requests: int
    total_assertions: int
    failed_assertions: int
    test_failures: tuple[TestFailure, ...]
    infrastructure_failure: InfrastructureFailure | None
    stdout: str
    stderr: str
    # PlaywrightAdapter: pytest tem um conceito de "skipped" que o Newman não
    # tem (ex.: endpoint que caiu no PlaceholderEndpointTestGenerator vira
    # @pytest.mark.skip) — default 0 preserva, sem exceção, todo construtor
    # existente do NewmanAdapter/testes, que nunca define este campo.
    skipped_tests: int = 0
