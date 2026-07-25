from pathlib import Path

from api_quality_agent.domain.exceptions import ResourceNotFoundError
from api_quality_agent.domain.models import ExecutionResultRecord
from api_quality_agent.ports.outbound import ExecutionResultReader


class LoadExecutionResultUseCase:
    def __init__(self, execution_result_reader: ExecutionResultReader) -> None:
        self._execution_result_reader = execution_result_reader

    def execute(self, *, input_path: str | None = None) -> ExecutionResultRecord:
        if input_path is not None:
            return self._execution_result_reader.read(path=Path(input_path))

        latest_path = self._execution_result_reader.find_latest()
        if latest_path is None:
            raise ResourceNotFoundError(
                "Nenhum resultado de execução foi encontrado.\n\n"
                "Execute primeiro:\n  api-quality-orchestrator run"
            )
        return self._execution_result_reader.read(path=latest_path)
