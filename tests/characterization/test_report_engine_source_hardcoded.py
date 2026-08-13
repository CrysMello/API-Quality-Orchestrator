"""Caracterização: `ReportEngine.generate_from_execution_summary` hoje grava
`source="newman"` fixo, independentemente da ferramenta real que produziu o
`result.json` (não existe outra ferramenta hoje). A Fase 9 do plano
Playwright torna esse campo dinâmico, a partir de `ExecutionResultRecord.tool`.

Se este teste quebrar durante a Fase 9, é a mudança esperada — troque-o para
verificar `source` dinâmico. Fora dessa fase, é regressão real.
"""

from datetime import datetime

from api_quality_agent.domain.models import ExecutionResultRecord
from api_quality_agent.reporting import ReportEngine


def _build_record() -> ExecutionResultRecord:
    return ExecutionResultRecord(
        source_path="artifacts/run_20260804_100000/result.json",
        schema_version="1.2",
        started_at=datetime(2026, 8, 4, 10, 0, 0),
        finished_at=datetime(2026, 8, 4, 10, 0, 2),
        duration_seconds=2.0,
        workspace_id="ws-1",
        workspace_name="QA Workspace",
        collection_id="col-1",
        collection_name="Pets Offline",
        total_requests=2,
        total_assertions=2,
        failed_assertions=0,
        success=True,
        infrastructure_failure=None,
        test_failures=(),
    )


def test_report_source_is_hardcoded_to_newman_today() -> None:
    report = ReportEngine().generate_from_execution_summary(_build_record())

    assert report.source == "newman"
    assert report.mode == "run"
