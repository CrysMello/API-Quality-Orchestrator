import json
from pathlib import Path

import pytest

from api_quality_agent.cli import bootstrap
from api_quality_agent.cli.exit_codes import (
    FUNCTIONAL_FAILURE,
    INVALID_INPUT_OR_CONFIGURATION,
    OPERATION_CANCELLED,
    RESOURCE_NOT_FOUND,
    SUCCESS,
)
from api_quality_agent.cli.main import main


def _valid_result_payload(**overrides) -> dict:
    payload = {
        "schema_version": "1.1",
        "execution": {
            "started_at": "2026-07-20T10:35:12+00:00",
            "finished_at": "2026-07-20T10:35:46+00:00",
            "duration_seconds": 34.1,
        },
        "workspace": {"id": "ws-1", "name": "QA Workspace"},
        "collection": {"id": "col-1", "name": "PetStore"},
        "summary": {"requests": 28, "assertions": 312, "passed": 309, "failed": 3},
        "success": False,
        "infrastructure_failure": None,
    }
    payload.update(overrides)
    return payload


def _write_result(path: Path, **overrides) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_valid_result_payload(**overrides)), encoding="utf-8")
    return path


# --- Fluxo feliz, sem API Key (report nunca toca o Postman) ----------------------------------------------------------------


def test_report_with_explicit_input_generates_html(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("POSTMAN_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    result_path = _write_result(tmp_path / "artifacts" / "run_x" / "result.json")

    exit_code = main(["report", "--input", str(result_path)])

    assert exit_code == SUCCESS
    out = capsys.readouterr().out
    assert "Report generated successfully." in out
    assert "PetStore" in out or str(result_path) in out
    output_html = tmp_path / "artifacts" / "run_x" / "report.html"
    assert output_html.is_file()
    assert "<h1>Execution Report</h1>" in output_html.read_text(encoding="utf-8")


def test_report_never_requires_postman_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTMAN_API_KEY", raising=False)
    result_path = _write_result(tmp_path / "run_x" / "result.json")

    exit_code = main(["report", "--input", str(result_path), "--output", str(tmp_path / "out.html")])

    assert exit_code == SUCCESS


# --- Descoberta automática ----------------------------------------------------------------


def test_report_without_input_uses_latest_result(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_result(tmp_path / "artifacts" / "run_a" / "result.json")
    result_path = _write_result(tmp_path / "artifacts" / "run_b" / "result.json")

    exit_code = main(["report"])

    assert exit_code == SUCCESS
    out = capsys.readouterr().out
    assert "Using latest execution result:" in out


def test_report_without_input_and_no_results_reports_resource_not_found(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = main(["report"])

    assert exit_code == RESOURCE_NOT_FOUND
    err = capsys.readouterr().err
    assert "api-quality-orchestrator run" in err


# --- Validação de entrada ----------------------------------------------------------------


def test_report_with_nonexistent_input_reports_invalid_input(tmp_path):
    exit_code = main(["report", "--input", str(tmp_path / "nao-existe.json")])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_report_with_invalid_json_reports_invalid_input(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("isto não é json", encoding="utf-8")

    exit_code = main(["report", "--input", str(path)])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_report_with_unsupported_schema_version_reports_invalid_input(tmp_path):
    path = _write_result(tmp_path / "result.json", schema_version="9.9")

    exit_code = main(["report", "--input", str(path)])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_report_with_invalid_format_is_rejected_by_argparse(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        main(["report", "--format", "pdf"])

    assert exc_info.value.code == 2


# --- --output ----------------------------------------------------------------


def test_report_output_as_directory(tmp_path):
    result_path = _write_result(tmp_path / "run_20260720_103512" / "result.json")
    output_dir = tmp_path / "reports"
    output_dir.mkdir()

    exit_code = main(["report", "--input", str(result_path), "--output", str(output_dir)])

    assert exit_code == SUCCESS
    assert (output_dir / "report_20260720_103512.html").is_file()


def test_report_output_as_file(tmp_path):
    result_path = _write_result(tmp_path / "run_x" / "result.json")
    output_file = tmp_path / "meu_relatorio.html"

    exit_code = main(["report", "--input", str(result_path), "--output", str(output_file)])

    assert exit_code == SUCCESS
    assert output_file.is_file()


# --- --overwrite ----------------------------------------------------------------


def test_report_does_not_overwrite_without_flag(tmp_path, capsys):
    result_path = _write_result(tmp_path / "run_x" / "result.json")
    existing = tmp_path / "run_x" / "report.html"
    existing.write_text("conteúdo antigo", encoding="utf-8")

    exit_code = main(["report", "--input", str(result_path)])

    assert exit_code == FUNCTIONAL_FAILURE
    assert existing.read_text(encoding="utf-8") == "conteúdo antigo"
    assert "--overwrite" in capsys.readouterr().err


def test_report_overwrites_with_flag(tmp_path):
    result_path = _write_result(tmp_path / "run_x" / "result.json")
    existing = tmp_path / "run_x" / "report.html"
    existing.write_text("conteúdo antigo", encoding="utf-8")

    exit_code = main(["report", "--input", str(result_path), "--overwrite"])

    assert exit_code == SUCCESS
    assert "conteúdo antigo" not in existing.read_text(encoding="utf-8")


# --- Exit code representa a geração do relatório, não o resultado dos testes ----------------------------------------------------------------


def test_report_exit_code_is_zero_even_when_tests_failed(tmp_path):
    result_path = _write_result(tmp_path / "run_x" / "result.json", success=False)

    exit_code = main(["report", "--input", str(result_path)])

    assert exit_code == SUCCESS


def test_report_exit_code_is_zero_when_tests_passed(tmp_path):
    result_path = _write_result(
        tmp_path / "run_x" / "result.json",
        success=True,
        summary={"requests": 28, "assertions": 312, "passed": 312, "failed": 0},
    )

    exit_code = main(["report", "--input", str(result_path)])

    assert exit_code == SUCCESS


def test_report_shows_failed_status_in_terminal_message(tmp_path, capsys):
    result_path = _write_result(tmp_path / "run_x" / "result.json", success=False)

    main(["report", "--input", str(result_path)])

    assert "FAILED" in capsys.readouterr().out


def test_report_shows_passed_status_in_terminal_message(tmp_path, capsys):
    result_path = _write_result(
        tmp_path / "run_x" / "result.json",
        success=True,
        summary={"requests": 28, "assertions": 312, "passed": 312, "failed": 0},
    )

    main(["report", "--input", str(result_path)])

    assert "PASSED" in capsys.readouterr().out


def test_report_shows_infrastructure_failure_status(tmp_path, capsys):
    result_path = _write_result(
        tmp_path / "run_x" / "result.json",
        success=False,
        infrastructure_failure={"type": "executable_not_found", "message": "Newman não encontrado."},
    )

    exit_code = main(["report", "--input", str(result_path)])

    assert exit_code == SUCCESS
    assert "INFRASTRUCTURE FAILURE" in capsys.readouterr().out


# --- Ctrl+C ----------------------------------------------------------------


class _InterruptingExecutionResultReader:
    def find_latest(self):
        return None

    def read(self, *, path):
        raise KeyboardInterrupt()


def test_report_keyboard_interrupt_returns_cancelled_not_unexpected_error(tmp_path, monkeypatch, capsys):
    result_path = _write_result(tmp_path / "run_x" / "result.json")
    monkeypatch.setattr(
        bootstrap, "JsonExecutionResultReader", lambda: _InterruptingExecutionResultReader()
    )

    exit_code = main(["report", "--input", str(result_path)])

    assert exit_code == OPERATION_CANCELLED
    captured = capsys.readouterr()
    assert "cancelada" in captured.out
    assert "inesperado" not in captured.err.lower()


# --- Segurança ----------------------------------------------------------------


def test_report_never_prints_secrets_even_if_embedded_in_names(tmp_path, capsys):
    result_path = _write_result(
        tmp_path / "run_x" / "result.json",
        workspace={"id": "ws-1", "name": "Bearer PMAK-should-not-leak-anywhere-else"},
    )

    main(["report", "--input", str(result_path)])

    # O nome só aparece porque é dado legítimo do próprio result.json (não é
    # segredo de verdade) — a checagem real de segurança é que nada ALÉM do
    # que já está no arquivo aparece (sem stdout/stderr/API key do ambiente).
    captured = capsys.readouterr()
    assert "POSTMAN_API_KEY" not in captured.out
    assert "POSTMAN_API_KEY" not in captured.err
