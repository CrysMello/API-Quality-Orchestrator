import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from api_quality_agent.domain.models import (
    ExecutionResult,
    ExecutionResultLocation,
    InfrastructureFailure,
    InfrastructureFailureType,
    TraceArtifact,
)
from api_quality_agent.ports.outbound import ExecutionResultRepository
from api_quality_agent.shared import sanitize_filename_component

# "1.0" (sem schema_version/workspace no arquivo), "1.1" (schema_version +
# workspace, aditivo), "1.2" (test_failures, aditivo), "1.3" (summary.skipped,
# aditivo — P1.1/skipped_tests), "1.4" (http_transactions, aditivo — P1.2),
# "1.5" (http_transactions[].test_id + assertion_results, aditivo — P1.1/
# detalhamento de assertions), "1.6" (trace_artifacts, aditivo — P1.3/Trace
# em falha) e "1.7" (evidence_failures, aditivo — P1.5/infrastructure
# failure das evidências) são as versões que api-quality-orchestrator
# report sabe ler — ver JsonExecutionResultReader. Mudanças de schema são
# sempre aditivas; nenhum campo existente é removido ou renomeado.
EXECUTION_RESULT_SCHEMA_VERSION = "1.7"

# Subdiretório, irmão de result.json, onde os .zip de trace (já mascarados
# pelo PlaywrightAdapter) são movidos — mesma convenção de "report.html ao
# lado de result.json" já usada por write_report.py (_resolve_output_path).
_TRACES_SUBDIR = "traces"

# P1.5: mesmo "source" usado pelo PlaywrightAdapter para falhas detectadas
# do lado dele (masking, trace ausente) — reaproveitado aqui para as
# falhas detectadas do lado da PERSISTÊNCIA (mkdir/move), nunca um valor
# novo/divergente para a mesma evidência.
_EVIDENCE_SOURCE_PLAYWRIGHT_TRACE = "playwright_trace"


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
                evidence_failures=result.evidence_failures,
            ),
            indent=2,
            ensure_ascii=False,
        )
        location = self._execution_result_repository.save(content=content)

        # P1.4 (hardening): se ALGUM trace não puder ser movido para o
        # destino final (diretório sem permissão, disco cheio, etc.), o
        # result.json já escrito não pode continuar afirmando que um
        # arquivo inexistente existe — regra explícita: nunca uma
        # referência falsa. P1.5: a falha em si (mkdir/move) também vira
        # uma InfrastructureFailure explícita — nunca só uma ausência
        # silenciosa em trace_artifacts. Reescreve (mesma _serialize,
        # nunca uma segunda fonte de verdade) só quando algo realmente
        # mudou.
        persisted_relative_paths, move_time_failures = _move_trace_artifacts(
            result.trace_artifacts, trace_relative_paths, location
        )
        if persisted_relative_paths != trace_relative_paths or move_time_failures:
            corrected_content = json.dumps(
                _serialize(
                    result,
                    collection_id=collection_id,
                    collection_name=collection_name,
                    started_at=started_at,
                    finished_at=finished_at,
                    workspace_id=workspace_id,
                    workspace_name=workspace_name,
                    trace_relative_paths=persisted_relative_paths,
                    evidence_failures=result.evidence_failures + move_time_failures,
                ),
                indent=2,
                ensure_ascii=False,
            )
            try:
                Path(location.path).write_text(corrected_content, encoding="utf-8")
            except OSError:
                # Melhor esforço: a correção em si é para um caso de borda
                # raro (falha ao mover um trace) — uma segunda falha aqui
                # nunca deve mascarar o resultado já persistido nem
                # levantar por cima da execução do teste em si.
                pass
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


def _evidence_failure(test_id: str, message: str) -> InfrastructureFailure:
    # Mensagens são sempre texto fixo escrito por este módulo (nunca a
    # mensagem bruta de uma OSError, que poderia ecoar um caminho de
    # arquivo local — não é um secret, mas também não agrega valor pro
    # usuário final) — nada aqui deriva do conteúdo do Trace em si.
    return InfrastructureFailure(
        failure_type=InfrastructureFailureType.EVIDENCE_PERSISTENCE_FAILED,
        message=message,
        source=_EVIDENCE_SOURCE_PLAYWRIGHT_TRACE,
        test_id=test_id,
    )


def _move_trace_artifacts(
    trace_artifacts: tuple[TraceArtifact, ...],
    relative_paths: tuple[str, ...],
    location: ExecutionResultLocation,
) -> tuple[tuple[str | None, ...], tuple[InfrastructureFailure, ...]]:
    # Devolve, na MESMA ordem/posição de trace_artifacts, o caminho
    # relativo que de fato foi movido com sucesso (ou None quando não foi
    # — mkdir falhou, ou o shutil.move específico falhou) e, à parte, uma
    # InfrastructureFailure por artefato que não pôde ser movido — nunca
    # só uma ausência silenciosa em trace_artifacts (P1.5).
    if not trace_artifacts:
        return (), ()
    run_dir = Path(location.path).resolve().parent
    traces_dir = run_dir / _TRACES_SUBDIR
    try:
        traces_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Nenhum trace pode ser movido sem o diretório de destino — nenhuma
        # referência falsa: todas as entradas voltam None, cada uma com
        # sua própria InfrastructureFailure correlacionada por test_id.
        failures = tuple(
            _evidence_failure(
                artifact.test_id,
                "Falha ao criar o diretório de destino do Trace; artefato não persistido.",
            )
            for artifact in trace_artifacts
        )
        return tuple(None for _ in trace_artifacts), failures

    persisted: list[str | None] = []
    move_failures: list[InfrastructureFailure] = []
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
            # persistido (None), nunca uma referência falsa, e a falha em
            # si vira uma InfrastructureFailure explícita.
            persisted.append(None)
            move_failures.append(
                _evidence_failure(
                    artifact.test_id,
                    "Falha ao mover o artefato de Trace para o destino final; "
                    "artefato não persistido.",
                )
            )
            continue
        persisted.append(relative_path)

    # O diretório temporário "masked" (criado pelo PlaywrightAdapter,
    # tempfile.mkdtemp) só é removido se ficou VAZIO — se algum arquivo não
    # foi movido (still lá dentro), rmdir falha sozinho e o diretório (com
    # a evidência que não pôde ser movida) é preservado para diagnóstico,
    # nunca destruído silenciosamente.
    for source_dir in source_dirs:
        try:
            source_dir.rmdir()
        except OSError:
            pass

    return tuple(persisted), tuple(move_failures)


def _serialize(
    result: ExecutionResult,
    *,
    collection_id: str | None,
    collection_name: str | None,
    started_at: datetime,
    finished_at: datetime,
    workspace_id: str | None,
    workspace_name: str | None,
    trace_relative_paths: tuple[str | None, ...],
    evidence_failures: tuple[InfrastructureFailure, ...],
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
        # test_failures: SEMPRE o resultado FUNCIONAL do teste (assertion,
        # status HTTP, schema) — nunca misturado com evidence_failures
        # (infraestrutura de captura de evidência). Ver regra fundamental
        # do P1.5: os dois são independentes.
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
        # relative_path is None: o arquivo físico não pôde ser movido para
        # o destino final (ver _move_trace_artifacts) — nunca uma
        # referência para um arquivo que não existe (regra explícita do
        # P1.4/hardening: "não criar referência falsa").
        "trace_artifacts": [
            {
                "type": artifact.type,
                "test_id": artifact.test_id,
                "path": relative_path,
            }
            for artifact, relative_path in zip(
                result.trace_artifacts, trace_relative_paths, strict=True
            )
            if relative_path is not None
        ],
        # P1.5 (infrastructure failure das evidências): distinta de
        # "infrastructure_failure" (singular, abaixo — falha da EXECUÇÃO
        # INTEIRA). Cada entrada aqui coexiste com um teste que pode ter
        # passado OU falhado normalmente; nunca decide o resultado do
        # teste, só sinaliza que uma evidência específica não pôde ser
        # capturada/mascarada/persistida com segurança.
        "evidence_failures": [
            {
                "type": failure.failure_type.value,
                "source": failure.source,
                "test_id": failure.test_id,
                "message": failure.message,
            }
            for failure in evidence_failures
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
