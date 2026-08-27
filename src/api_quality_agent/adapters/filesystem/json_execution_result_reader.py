import json
from datetime import datetime
from pathlib import Path
from typing import Any

from api_quality_agent.domain.exceptions import (
    InputFileNotFoundError,
    InvalidExecutionResultError,
    InvalidJsonError,
    UnsupportedExecutionResultSchemaError,
)
from api_quality_agent.domain.models import (
    AssertionResult,
    ExecutionResultRecord,
    HttpTransaction,
    HttpTransactionHeader,
    InfrastructureFailure,
    InfrastructureFailureType,
    TestFailure,
    TraceArtifact,
)

DEFAULT_EXECUTION_RESULTS_BASE_PATH = Path("artifacts")

# "1.0" nunca teve schema_version no arquivo (assumido implicitamente) nem
# workspace; "1.1" adiciona workspace; "1.2" adiciona test_failures; "1.3"
# adiciona summary.skipped (P1.1/skipped_tests); "1.4" adiciona
# http_transactions (P1.2); "1.5" adiciona http_transactions[].test_id e
# assertion_results (P1.1/detalhamento de assertions); "1.6" adiciona
# trace_artifacts (P1.3/Trace em falha); "1.7" adiciona evidence_failures
# (P1.5/infrastructure failure das evidências); "1.8" adiciona
# http_transactions[].query_parameters (P2.1/evidência HTTP estruturada).
# Cada versão é aditiva sobre a anterior. Qualquer outra versão é
# recusada — nunca interpretada parcialmente.
_SUPPORTED_SCHEMA_VERSIONS = frozenset(
    {"1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"}
)
_DEFAULT_SCHEMA_VERSION = "1.0"


class JsonExecutionResultReader:
    def __init__(self, base_path: Path | None = None) -> None:
        self._base_path = base_path or DEFAULT_EXECUTION_RESULTS_BASE_PATH

    def find_latest(self) -> Path | None:
        if not self._base_path.is_dir():
            return None
        candidates = list(self._base_path.glob("**/result.json"))
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def read(self, *, path: Path) -> ExecutionResultRecord:
        if not path.is_file():
            raise InputFileNotFoundError(f"Arquivo de resultado não encontrado: {path}")

        raw_text = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise InvalidJsonError(f"Arquivo de resultado com JSON inválido: {path}") from exc

        if not isinstance(payload, dict):
            raise InvalidExecutionResultError(
                f"O arquivo informado não contém um resultado de execução válido: {path}"
            )

        schema_version = payload.get("schema_version", _DEFAULT_SCHEMA_VERSION)
        if schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
            raise UnsupportedExecutionResultSchemaError(
                f"Versão de resultado não suportada: {schema_version}"
            )

        return _deserialize(payload, schema_version=schema_version, source_path=str(path))


def _deserialize(payload: dict[str, Any], *, schema_version: str, source_path: str) -> ExecutionResultRecord:
    try:
        execution = _require_dict(payload, "execution")
        collection = _require_dict(payload, "collection")
        summary = _require_dict(payload, "summary")
        success = payload["success"]
        if not isinstance(success, bool):
            raise TypeError("'success' deve ser booleano")

        # "workspace" só existe a partir do schema 1.1 — ausente em 1.0,
        # tratado como desconhecido (None/None), nunca inventado.
        workspace = payload.get("workspace") or {}

        infrastructure_failure_payload = payload.get("infrastructure_failure")
        infrastructure_failure = None
        if infrastructure_failure_payload is not None:
            infrastructure_failure = InfrastructureFailure(
                failure_type=InfrastructureFailureType(infrastructure_failure_payload["type"]),
                message=infrastructure_failure_payload["message"],
            )

        # "test_failures" só existe a partir do schema 1.2 — ausente em 1.0/
        # 1.1, tratado como lista vazia, nunca inventado.
        test_failures = tuple(
            TestFailure(
                request_name=failure["request_name"],
                test_name=failure["test_name"],
                error_message=failure["error_message"],
            )
            for failure in payload.get("test_failures") or []
        )

        # "summary.skipped" só existe a partir do schema 1.3 — ausente em
        # 1.0/1.1/1.2, tratado como 0, nunca inventado.
        skipped_tests = int(summary.get("skipped", 0))

        # "http_transactions" só existe a partir do schema 1.4 — ausente em
        # 1.0/1.1/1.2/1.3, tratado como tupla vazia, nunca inventado.
        # ".test_id" só existe a partir do 1.5 — ausente em 1.4, tratado
        # como "" (mesmo default de HttpTransaction.test_id).
        # ".query_parameters" só existe a partir do 1.8 — ausente em
        # 1.4-1.7, tratado como tupla vazia (mesmo default de
        # HttpTransaction.query_parameters), nunca inventado.
        http_transactions = tuple(
            HttpTransaction(
                test_id=transaction.get("test_id") or "",
                method=transaction["method"],
                url=transaction["url"],
                request_headers=_headers_from_payload(transaction.get("request_headers")),
                request_body=transaction.get("request_body"),
                query_parameters=_headers_from_payload(transaction.get("query_parameters")),
                response_status=int(transaction["response_status"]),
                response_headers=_headers_from_payload(transaction.get("response_headers")),
                response_body=transaction.get("response_body"),
            )
            for transaction in payload.get("http_transactions") or []
        )

        # "assertion_results" só existe a partir do schema 1.5 — ausente em
        # 1.0-1.4, tratado como tupla vazia, nunca inventado.
        assertion_results = tuple(
            AssertionResult(
                test_id=assertion_result["test_id"],
                name=assertion_result["name"],
                expected=assertion_result["expected"],
                actual=assertion_result["actual"],
                status=assertion_result["status"],
                precision=assertion_result["precision"],
                reason=assertion_result["reason"],
            )
            for assertion_result in payload.get("assertion_results") or []
        )

        # "trace_artifacts" só existe a partir do schema 1.6 — ausente em
        # 1.0-1.5, tratado como tupla vazia, nunca inventado.
        trace_artifacts = tuple(
            TraceArtifact(
                type=trace_artifact["type"],
                test_id=trace_artifact["test_id"],
                path=trace_artifact["path"],
            )
            for trace_artifact in payload.get("trace_artifacts") or []
        )

        # "evidence_failures" só existe a partir do schema 1.7 — ausente em
        # 1.0-1.6, tratado como tupla vazia, nunca inventado.
        evidence_failures = tuple(
            InfrastructureFailure(
                failure_type=InfrastructureFailureType(evidence_failure["type"]),
                message=evidence_failure["message"],
                source=evidence_failure.get("source"),
                test_id=evidence_failure.get("test_id"),
            )
            for evidence_failure in payload.get("evidence_failures") or []
        )

        return ExecutionResultRecord(
            source_path=source_path,
            schema_version=schema_version,
            started_at=datetime.fromisoformat(execution["started_at"]),
            finished_at=datetime.fromisoformat(execution["finished_at"]),
            duration_seconds=float(execution["duration_seconds"]),
            workspace_id=workspace.get("id"),
            workspace_name=workspace.get("name"),
            collection_id=collection.get("id"),
            collection_name=collection.get("name"),
            total_requests=int(summary["requests"]),
            total_assertions=int(summary["assertions"]),
            failed_assertions=int(summary["failed"]),
            skipped_tests=skipped_tests,
            success=success,
            infrastructure_failure=infrastructure_failure,
            test_failures=test_failures,
            http_transactions=http_transactions,
            assertion_results=assertion_results,
            trace_artifacts=trace_artifacts,
            evidence_failures=evidence_failures,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidExecutionResultError(
            f"O arquivo informado não contém um resultado de execução válido: {exc}"
        ) from exc


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload[key]
    if not isinstance(value, dict):
        raise TypeError(f"'{key}' deve ser um objeto")
    return value


def _headers_from_payload(raw_headers: Any) -> tuple[HttpTransactionHeader, ...]:
    if not isinstance(raw_headers, dict):
        return ()
    return tuple(
        HttpTransactionHeader(name=name, value=value) for name, value in raw_headers.items()
    )
