import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from api_quality_agent.domain.models import ExecutionResult, ExecutionResultLocation, TraceArtifact
from api_quality_agent.ports.outbound import ExecutionResultRepository
from api_quality_agent.shared import sanitize_filename_component

# "1.0" (sem schema_version/workspace no arquivo), "1.1" (schema_version +
# workspace, aditivo), "1.2" (test_failures, aditivo), "1.3" (summary.skipped,
# aditivo — P1.1/skipped_tests), "1.4" (http_transactions, aditivo — P1.2),
# "1.5" (http_transactions[].test_id + assertion_results, aditivo — P1.1/
# detalhamento de assertions) e "1.6" (trace_artifacts, aditivo — P1.3/Trace
# em falha) são as versões que api-quality-orchestrator report sabe ler —
# ver JsonExecutionResultReader. Mudanças de schema são sempre aditivas;
# nenhum campo existente é removido ou renomeado.
EXECUTION_RESULT_SCHEMA_VERSION = "1.6"

# Subdiretório, irmão de result.json, onde os .zip de trace (já mascarados
# pelo PlaywrightAdapter) são movidos — mesma convenção de "report.html ao
# lado de result.json" já usada por write_report.py (_resolve_output_path).
_TRACES_SUBDIR = "traces"


class PersistExecutionResultUseCase:
    def __init__(self, execution_result_repository: ExecutionResultRepository) -> None:
        self._execution_result_repository = execution_result_repository

    def execute(
        self,
        result: ExecutionResult,
        *,
        collection_id: str | None,
        collection_name: str | None,
        started_at: datetime,
        finished_at: datetime,
        workspace_id: str | None = None,
        workspace_name: str | None = None,
    ) -> ExecutionResultLocation:
        # Nomes relativos ("traces/xxx.zip") calculados ANTES de serializar
        # o JSON — result.json precisa conter o caminho final já na mesma
        # escrita, mas o diretório ABSOLUTO só é conhecido depois de
        # repository.save() (ele decide o nome do diretório run_<timestamp>,
        # ver JsonExecutionResultRepository). O fragmento relativo não
        # depende disso, só do próprio test_id — por isso pode ser
        # calculado aqui e usado nos dois lugares (conteúdo serializado e
        # destino físico do shutil.move abaixo) sem duas fontes divergentes.
        trace_relative_paths = _resolve_trace_relative_paths(result.trace_artifacts)

        content = json.dumps(
            _serialize(
                result,
                collection_id=collection_id,
                collection_name=collection_name,
                started_at=started_at,
                finished_at=finished_at,
                workspace_id=workspace_id,
                workspace_name=workspace_name,
                trace_relative_paths=trace_relative_paths,
            ),
            indent=2,
            ensure_ascii=False,
        )
        location = self._execution_result_repository.save(content=content)
        _move_trace_artifacts(result.trace_artifacts, trace_relative_paths, location)
        return location


def _resolve_trace_relative_paths(trace_artifacts: tuple[TraceArtifact, ...]) -> tuple[str, ...]:
    # "traces/<índice>-<test_id-sanitizado>.zip" — o índice garante nome
    # único mesmo se dois test_ids diferentes sanitizassem para a mesma
    # string (caso extremo, nunca observado, mas nunca assumido
    # impossível); a correlação de verdade com test_id continua sendo
    # sempre o campo "test_id" dentro de result.json, nunca este nome de
    # arquivo (regra explícita do bloco P1.3).
    paths = []
    for index, artifact in enumerate(trace_artifacts):
        sanitized = sanitize_filename_component(
            artifact.test_id, max_length=60, hash_length=8, fallback="trace"
        )
        paths.append(f"{_TRACES_SUBDIR}/{index:02d}-{sanitized}.zip")
    return tuple(paths)


def _move_trace_artifacts(
    trace_artifacts: tuple[TraceArtifact, ...],
    relative_paths: tuple[str, ...],
    location: ExecutionResultLocation,
) -> None:
    if not trace_artifacts:
        return
    run_dir = Path(location.path).resolve().parent
    traces_dir = run_dir / _TRACES_SUBDIR
    try:
        traces_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # result.json já foi gravado com sucesso nesse ponto (referências
        # incluídas) — uma falha só ao criar a pasta física nunca desfaz
        # isso; os .zip ficam órfãos no temp do SO, mesmo compromisso já
        # aceito pelo resto deste método.
        return

    source_dirs: set[Path] = set()
    for artifact, relative_path in zip(trace_artifacts, relative_paths, strict=True):
        source = Path(artifact.path)
        source_dirs.add(source.parent)
        try:
            shutil.move(str(source), str(run_dir / relative_path))
        except OSError:
            # Mesma postura defensiva de _persist_result (run_command.py):
            # uma falha ao mover um artefato de evidência nunca transforma
            # uma execução bem-sucedida (ou com falhas de teste) em erro —
            # só este trace específico fica ausente do resultado
            # persistido, o restante do result.json já foi gravado.
            continue

    # O diretório temporário "masked" (criado pelo PlaywrightAdapter,
    # tempfile.mkdtemp) só existe para hospedar estes arquivos até aqui —
    # depois de movidos, é limpo; nunca falha a persistência por causa
    # disso (best-effort, mesmo espírito do resto deste método).
    for source_dir in source_dirs:
        shutil.rmtree(source_dir, ignore_errors=True)


def _serialize(
    result: ExecutionResult,
    *,
    collection_id: str | None,
    collection_name: str | None,
    started_at: datetime,
    finished_at: datetime,
    workspace_id: str | None,
    workspace_name: str | None,
    trace_relative_paths: tuple[str, ...],
) -> dict[str, Any]:
    # Serialização explícita e estruturada: nunca stdout/stderr brutos, nunca
    # a Collection completa — só os campos já expostos pelo domínio, usados
    # como entrada oficial de `api-quality-orchestrator report`. collection_id/
    # workspace_* ficam None quando a execução veio de um arquivo local
    # (run --file) — não há Workspace/Collection do Postman envolvidos.
    infrastructure_failure = result.infrastructure_failure
    return {
        "schema_version": EXECUTION_RESULT_SCHEMA_VERSION,
        "execution": {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": result.duration_seconds,
        },
        "workspace": {
            "id": workspace_id,
            "name": workspace_name,
        },
        "collection": {
            "id": collection_id,
            "name": collection_name,
        },
        "summary": {
            "requests": result.total_requests,
            "assertions": result.total_assertions,
            "passed": result.total_assertions - result.failed_assertions,
            "failed": result.failed_assertions,
            "skipped": result.skipped_tests,
        },
        "test_failures": [
            {
                "request_name": failure.request_name,
                "test_name": failure.test_name,
                "error_message": failure.error_message,
            }
            for failure in result.test_failures
        ],
        "http_transactions": [
            {
                "test_id": transaction.test_id,
                "method": transaction.method,
                "url": transaction.url,
                "request_headers": {
                    header.name: header.value for header in transaction.request_headers
                },
                "request_body": transaction.request_body,
                "response_status": transaction.response_status,
                "response_headers": {
                    header.name: header.value for header in transaction.response_headers
                },
                "response_body": transaction.response_body,
            }
            for transaction in result.http_transactions
        ],
        "assertion_results": [
            {
                "test_id": assertion_result.test_id,
                "name": assertion_result.name,
                "expected": assertion_result.expected,
                "actual": assertion_result.actual,
                "status": assertion_result.status,
                "precision": assertion_result.precision,
                "reason": assertion_result.reason,
            }
            for assertion_result in result.assertion_results
        ],
        "trace_artifacts": [
            {
                "type": artifact.type,
                "test_id": artifact.test_id,
                "path": relative_path,
            }
            for artifact, relative_path in zip(
                result.trace_artifacts, trace_relative_paths, strict=True
            )
        ],
        "success": result.success,
        "infrastructure_failure": (
            {
                "type": infrastructure_failure.failure_type.value,
                "message": infrastructure_failure.message,
            }
            if infrastructure_failure is not None
            else None
        ),
    }
