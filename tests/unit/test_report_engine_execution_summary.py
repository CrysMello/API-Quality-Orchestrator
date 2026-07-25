from datetime import datetime, timezone

from api_quality_agent.domain.models import (
    ExecutionResultRecord,
    InfrastructureFailure,
    InfrastructureFailureType,
)
from api_quality_agent.reporting import ReportEngine

_STARTED_AT = datetime(2026, 7, 20, 10, 35, 12, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 7, 20, 10, 35, 46, tzinfo=timezone.utc)


def _record(**overrides) -> ExecutionResultRecord:
    defaults = dict(
        source_path="artifacts/run_20260720_103512123456/result.json",
        schema_version="1.1",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
        duration_seconds=34.1,
        workspace_id="ws-1",
        workspace_name="QA Workspace",
        collection_id="col-1",
        collection_name="PetStore",
        total_requests=28,
        total_assertions=312,
        failed_assertions=3,
        success=False,
        infrastructure_failure=None,
        test_failures=(),
    )
    defaults.update(overrides)
    return ExecutionResultRecord(**defaults)


def test_report_carries_workspace_and_collection_from_record():
    engine = ReportEngine(clock=lambda: datetime(2026, 7, 20, 11, 0, 0, tzinfo=timezone.utc))

    report = engine.generate_from_execution_summary(_record())

    assert report.workspace_id == "ws-1"
    assert report.workspace_name == "QA Workspace"
    assert report.collection_id == "col-1"
    assert report.collection_name == "PetStore"


def test_generation_only_sections_are_semantically_empty_not_invented():
    engine = ReportEngine()

    report = engine.generate_from_execution_summary(_record())

    assert report.endpoints == ()
    assert report.analysis_warnings == ()
    assert report.diff.has_changes is False
    assert report.diff.entries == ()
    assert report.update.attempted is False
    assert report.update.approved is None
    assert report.artifacts == ()


def test_execution_section_reflects_the_record():
    engine = ReportEngine()

    report = engine.generate_from_execution_summary(_record())

    execution = report.execution
    assert execution.executed is True
    assert execution.success is False
    assert execution.total_requests == 28
    assert execution.total_assertions == 312
    assert execution.failed_assertions == 3
    assert execution.started_at == _STARTED_AT
    assert execution.finished_at == _FINISHED_AT
    # Detalhamento por teste nunca foi persistido no result.json — não pode
    # ser inventado aqui.
    assert execution.test_failures == ()


def test_execution_id_is_derived_from_source_directory_name():
    engine = ReportEngine()

    report = engine.generate_from_execution_summary(_record())

    assert report.execution_id == "20260720_103512123456"


def test_infrastructure_failure_is_propagated():
    engine = ReportEngine()
    record = _record(
        success=False,
        total_requests=0,
        total_assertions=0,
        failed_assertions=0,
        infrastructure_failure=InfrastructureFailure(
            failure_type=InfrastructureFailureType.EXECUTABLE_NOT_FOUND,
            message="Executável do Newman não encontrado.",
        ),
    )

    report = engine.generate_from_execution_summary(record)

    assert report.execution.infrastructure_failure is not None
    assert report.execution.infrastructure_failure.failure_type == "executable_not_found"
    assert report.execution.infrastructure_failure.message == "Executável do Newman não encontrado."


def test_workspace_missing_from_record_results_in_none_not_a_placeholder_string():
    engine = ReportEngine()
    record = _record(workspace_id=None, workspace_name=None)

    report = engine.generate_from_execution_summary(record)

    assert report.workspace_id is None
    assert report.workspace_name is None


def test_render_execution_summary_html_is_the_single_facade_used_by_the_cli():
    # report_command.py chama só este método (nunca importa reporting/*
    # diretamente) — cobre build + render numa única chamada.
    engine = ReportEngine()

    html = engine.render_execution_summary_html(_record())

    assert "<h1>Execution Report</h1>" in html
    assert "PetStore" in html
    assert "QA Workspace" in html
