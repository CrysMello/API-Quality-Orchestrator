from datetime import datetime, timezone

from api_quality_agent.domain.models import ExecutionResultRecord
from api_quality_agent.reporting import ReportEngine, render_execution_report_html

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


def _render(record: ExecutionResultRecord) -> str:
    report = ReportEngine().generate_from_execution_summary(record)
    return render_execution_report_html(
        report, source_path=record.source_path, schema_version=record.schema_version
    )


def test_html_is_self_contained_no_remote_resources():
    html = _render(_record())

    assert "<script" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "<link" not in html  # sem folha de estilo remota


def test_html_shows_passed_status_with_text_not_only_color():
    html = _render(_record(success=True, failed_assertions=0))

    assert "PASSED" in html


def test_html_shows_failed_status_with_text():
    html = _render(_record(success=False))

    assert "FAILED" in html


def test_html_shows_infrastructure_failure_status():
    from api_quality_agent.domain.models import InfrastructureFailure, InfrastructureFailureType

    record = _record(
        success=False,
        total_requests=0,
        total_assertions=0,
        failed_assertions=0,
        infrastructure_failure=InfrastructureFailure(
            failure_type=InfrastructureFailureType.EXECUTABLE_NOT_FOUND,
            message="Newman não encontrado.",
        ),
    )

    html = _render(record)

    assert "INFRASTRUCTURE FAILURE" in html
    assert "Newman não encontrado." in html


def test_html_shows_cards_with_summary_numbers():
    html = _render(_record(total_requests=28, total_assertions=312, failed_assertions=3))

    assert "28" in html
    assert "312" in html
    assert "309" in html  # passed = 312 - 3
    assert "3" in html


def test_html_shows_workspace_and_collection_names():
    html = _render(_record(workspace_name="QA Workspace", collection_name="PetStore"))

    assert "QA Workspace" in html
    assert "PetStore" in html


def test_html_shows_na_when_workspace_missing():
    html = _render(_record(workspace_id=None, workspace_name=None))

    assert "N/A" in html


def test_html_shows_started_and_finished_timestamps():
    html = _render(_record())

    assert "2026-07-20 10:35:12" in html
    assert "2026-07-20 10:35:46" in html


def test_html_shows_no_failures_message_when_there_are_none():
    html = _render(_record(failed_assertions=0, success=True))

    assert "Nenhuma falha encontrada." in html


def test_html_shows_failure_count_without_inventing_per_test_details():
    html = _render(_record(failed_assertions=3, success=False))

    assert "3 assertion(s) falharam" in html
    assert "não está disponível" in html


def test_html_shows_metadata_source_and_schema():
    html = _render(_record())

    assert "artifacts/run_20260720_103512123456/result.json" in html
    assert "1.1" in html
    assert "HTML" in html


# --- Escaping / segurança ----------------------------------------------------------------


def test_html_escapes_collection_name_containing_script_tag():
    record = _record(collection_name='<script>alert("xss")</script>')

    html = _render(record)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_html_escapes_workspace_name_containing_markup():
    record = _record(workspace_name='"><img src=x onerror=alert(1)>')

    html = _render(record)

    assert "<img src=x" not in html


def test_html_escapes_infrastructure_failure_message():
    from api_quality_agent.domain.models import InfrastructureFailure, InfrastructureFailureType

    record = _record(
        infrastructure_failure=InfrastructureFailure(
            failure_type=InfrastructureFailureType.UNEXPECTED_ERROR,
            message="<script>steal()</script>",
        )
    )

    html = _render(record)

    assert "<script>steal()" not in html


def test_html_never_contains_api_key_like_strings():
    # O ExecutionResultRecord estruturalmente não carrega segredos (nunca
    # persistidos em result.json), mas confirmamos aqui que nada parecido
    # com uma API Key aparece no HTML final.
    html = _render(_record())

    assert "PMAK-" not in html
    assert "Authorization" not in html
    assert "Bearer" not in html
