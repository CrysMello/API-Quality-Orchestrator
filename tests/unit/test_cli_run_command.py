import json
from pathlib import Path

import pytest
from conftest import (
    COLLECTION_A_ID,
    COLLECTION_A_NAME,
    COLLECTION_B_ID,
    COLLECTION_B_NAME,
    FAKE_API_KEY,
    configure_server,
)

from api_quality_agent.cli import bootstrap
from api_quality_agent.cli.exit_codes import (
    AMBIGUOUS_SELECTION,
    FUNCTIONAL_FAILURE,
    INTEGRATION_FAILURE,
    INVALID_INPUT_OR_CONFIGURATION,
    OPERATION_CANCELLED,
    RESOURCE_NOT_FOUND,
    SUCCESS,
)
from api_quality_agent.cli.main import main


# --- Seleção por ID/nome/índice/interativa (reaproveitada de generate/update) --------------------


def test_run_by_valid_id_succeeds(cli_env, selected_workspace, fake_newman, monkeypatch, capsys):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS
    assert "Execution finished successfully." in capsys.readouterr().out


def test_run_by_invalid_id_reports_resource_not_found(cli_env, selected_workspace, fake_newman):
    configure_server(cli_env)

    exit_code = main(["run", "--collection-id", "id-inexistente"])

    assert exit_code == RESOURCE_NOT_FOUND


def test_run_by_valid_name_succeeds(cli_env, selected_workspace, fake_newman, monkeypatch):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-name", COLLECTION_B_NAME])

    assert exit_code == SUCCESS


def test_run_by_duplicate_name_reports_ambiguous_selection(cli_env, selected_workspace, fake_newman):
    configure_server(cli_env, duplicate_name=True)

    exit_code = main(["run", "--collection-name", COLLECTION_A_NAME])

    assert exit_code == AMBIGUOUS_SELECTION


def test_run_by_valid_index_succeeds(cli_env, selected_workspace, fake_newman, monkeypatch):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    # Ordenação por nome: [1] Orders API, [2] Pets API.
    exit_code = main(["run", "1"])

    assert exit_code == SUCCESS


@pytest.mark.parametrize("index", ["0", "-1", "99"])
def test_run_by_out_of_range_index_reports_invalid_input(cli_env, selected_workspace, fake_newman, index):
    configure_server(cli_env)

    exit_code = main(["run", index])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_run_interactive_valid_choice_succeeds(cli_env, selected_workspace, fake_newman, monkeypatch):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    monkeypatch.setattr("builtins.input", lambda *_: "1")

    exit_code = main(["run"])

    assert exit_code == SUCCESS


def test_run_interactive_invalid_then_valid_choice_succeeds(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    responses = iter(["abc", "99", "2"])
    monkeypatch.setattr("builtins.input", lambda *_: next(responses))

    exit_code = main(["run"])

    assert exit_code == SUCCESS


def test_run_rejects_id_and_index_together(cli_env, selected_workspace, fake_newman):
    configure_server(cli_env)

    exit_code = main(["run", "1", "--collection-id", COLLECTION_A_ID])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_run_rejects_id_and_name_together(cli_env, selected_workspace, fake_newman):
    configure_server(cli_env)

    exit_code = main(
        ["run", "--collection-id", COLLECTION_A_ID, "--collection-name", COLLECTION_B_NAME]
    )

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


# --- Precedência do executável do Newman ----------------------------------------------------------------


def test_run_newman_executable_flag_is_used_when_provided(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    main(["run", "--collection-id", COLLECTION_A_ID, "--newman-executable", "caminho-da-flag"])

    assert fake_newman.captured_executables == ["caminho-da-flag"]


def test_run_newman_executable_env_var_is_used_when_flag_absent(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    monkeypatch.setenv("NEWMAN_EXECUTABLE", "caminho-da-env")

    main(["run", "--collection-id", COLLECTION_A_ID])

    assert fake_newman.captured_executables == ["caminho-da-env"]


def test_run_falls_back_to_default_newman_when_nothing_configured(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    monkeypatch.delenv("NEWMAN_EXECUTABLE", raising=False)

    main(["run", "--collection-id", COLLECTION_A_ID])

    assert fake_newman.captured_executables == ["newman"]


def test_run_newman_executable_flag_takes_precedence_over_env_var(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    monkeypatch.setenv("NEWMAN_EXECUTABLE", "caminho-da-env")

    main(
        [
            "run",
            "--collection-id",
            COLLECTION_A_ID,
            "--newman-executable",
            "caminho-da-flag",
        ]
    )

    assert fake_newman.captured_executables == ["caminho-da-flag"]


def test_run_newman_executable_accepts_windows_path_with_spaces(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    windows_path = r"C:\Program Files\nodejs\newman.cmd"

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID, "--newman-executable", windows_path])

    assert exit_code == SUCCESS
    assert fake_newman.captured_executables == [windows_path]


# --- Interpretação do ExecutionResult / mapeamento de exit code ----------------------------------------------------------------


def test_run_success_returns_zero(cli_env, selected_workspace, fake_newman, monkeypatch):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS


def test_run_with_test_failures_returns_functional_failure(
    cli_env, selected_workspace, fake_newman, monkeypatch, capsys
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "with_test_failures")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == FUNCTIONAL_FAILURE
    out = capsys.readouterr().out
    assert "Execution finished with test failures." in out
    assert "Traceback" not in out


def test_run_with_executable_not_found_returns_integration_failure_and_guidance(
    cli_env, selected_workspace, capsys
):
    # Sem o fixture fake_newman: usa um NewmanAdapter real apontado para um
    # executável que não existe, provando a classificação real de falha de
    # infraestrutura (nenhum mock da lógica de detecção).
    configure_server(cli_env)

    exit_code = main(
        ["run", "--collection-id", COLLECTION_A_ID, "--newman-executable", "newman-que-nao-existe"]
    )

    assert exit_code == INTEGRATION_FAILURE
    out = capsys.readouterr().out
    assert "infrastructure error" in out
    assert "--newman-executable" in out
    assert "NEWMAN_EXECUTABLE" in out


def test_run_infrastructure_failure_is_not_classified_as_test_failure(
    cli_env, selected_workspace, capsys
):
    configure_server(cli_env)

    exit_code = main(
        ["run", "--collection-id", COLLECTION_A_ID, "--newman-executable", "newman-que-nao-existe"]
    )

    assert exit_code != FUNCTIONAL_FAILURE
    assert "test failures" not in capsys.readouterr().out


# --- Resumo no terminal ----------------------------------------------------------------


def test_run_summary_shows_available_fields(
    cli_env, selected_workspace, fake_newman, monkeypatch, capsys
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS
    out = capsys.readouterr().out
    assert "Execution Summary" in out
    assert "Workspace" in out
    assert "Collection" in out
    assert "Started" in out
    assert "Finished" in out
    assert "Duration" in out
    assert "Requests" in out
    assert "Assertions" in out
    assert "Passed" in out
    assert "Failed" in out


def test_run_summary_never_shows_artifacts_line(
    cli_env, selected_workspace, fake_newman, monkeypatch, capsys
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS
    assert "Artifacts" not in capsys.readouterr().out


def test_run_infrastructure_failure_does_not_print_full_summary(
    cli_env, selected_workspace, capsys
):
    configure_server(cli_env)

    main(["run", "--collection-id", COLLECTION_A_ID, "--newman-executable", "newman-que-nao-existe"])

    out = capsys.readouterr().out
    assert "Execution Summary" not in out


# --- Ctrl+C ----------------------------------------------------------------


def test_run_keyboard_interrupt_during_interactive_selection_returns_cancelled(
    cli_env, selected_workspace, fake_newman, monkeypatch, capsys
):
    configure_server(cli_env)

    def _raise_keyboard_interrupt(*_a, **_k):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", _raise_keyboard_interrupt)

    exit_code = main(["run"])

    assert exit_code == OPERATION_CANCELLED
    captured = capsys.readouterr()
    assert "cancelada" in captured.out
    assert "inesperado" not in captured.err.lower()


def test_run_keyboard_interrupt_during_execution_returns_cancelled_not_unexpected_error(
    cli_env, selected_workspace, monkeypatch, capsys
):
    configure_server(cli_env)

    class _InterruptingRunner:
        def run(self, **_kwargs):
            raise KeyboardInterrupt()

    monkeypatch.setattr(bootstrap, "NewmanAdapter", lambda **_kwargs: _InterruptingRunner())

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == OPERATION_CANCELLED
    captured = capsys.readouterr()
    assert "cancelada" in captured.out
    assert "inesperado" not in captured.err.lower()


# --- Segurança ----------------------------------------------------------------


def test_run_never_leaks_api_key(cli_env, selected_workspace, fake_newman, monkeypatch, capsys):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS
    captured = capsys.readouterr()
    assert FAKE_API_KEY not in captured.out
    assert FAKE_API_KEY not in captured.err


def test_run_never_prints_raw_test_failure_message_with_secret(
    cli_env, selected_workspace, fake_newman, monkeypatch, capsys
):
    # O NewmanAdapter já mascara segredos do Environment antes de devolver o
    # resultado; aqui confirmamos que o comando run também não imprime
    # stdout/stderr brutos (onde o segredo apareceria) no resumo.
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "test_failures_with_secret")
    monkeypatch.setenv("FAKE_NEWMAN_SECRET_VALUE", "segredo-super-sensivel")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == FUNCTIONAL_FAILURE
    captured = capsys.readouterr()
    assert "segredo-super-sensivel" not in captured.out
    assert "segredo-super-sensivel" not in captured.err


# --- Persistência do resultado (result.json) ----------------------------------------------------------------


def _find_result_json(tmp_path: Path) -> Path:
    matches = list(tmp_path.glob("artifacts/run_*/result.json"))
    assert len(matches) == 1, f"esperado exatamente 1 result.json, achei {matches}"
    return matches[0]


def test_run_success_persists_result_json(cli_env, selected_workspace, fake_newman, monkeypatch, tmp_path, capsys):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS
    result_path = _find_result_json(tmp_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["success"] is True
    assert payload["collection"] == {"id": COLLECTION_A_ID, "name": COLLECTION_A_NAME}
    assert payload["summary"]["failed"] == 0
    assert payload["infrastructure_failure"] is None
    out = capsys.readouterr().out
    assert "Result saved to:" in out
    assert str(result_path) in out


def test_run_with_test_failures_also_persists_result_json(
    cli_env, selected_workspace, fake_newman, monkeypatch, tmp_path
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "with_test_failures")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == FUNCTIONAL_FAILURE
    result_path = _find_result_json(tmp_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["success"] is False
    assert payload["summary"]["failed"] == 1


def test_run_infrastructure_failure_does_not_persist_result_json(
    cli_env, selected_workspace, tmp_path
):
    configure_server(cli_env)

    exit_code = main(
        ["run", "--collection-id", COLLECTION_A_ID, "--newman-executable", "newman-que-nao-existe"]
    )

    assert exit_code == INTEGRATION_FAILURE
    assert list(tmp_path.glob("artifacts/run_*/result.json")) == []


def test_run_two_executions_produce_distinct_result_files(
    cli_env, selected_workspace, fake_newman, monkeypatch, tmp_path
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    main(["run", "--collection-id", COLLECTION_A_ID])
    main(["run", "--collection-id", COLLECTION_A_ID])

    matches = list(tmp_path.glob("artifacts/run_*/result.json"))
    assert len(matches) == 2
    assert matches[0] != matches[1]


def test_run_persisted_json_never_contains_api_key(
    cli_env, selected_workspace, fake_newman, monkeypatch, tmp_path
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    main(["run", "--collection-id", COLLECTION_A_ID])

    result_path = _find_result_json(tmp_path)
    assert FAKE_API_KEY not in result_path.read_text(encoding="utf-8")


def test_run_write_failure_does_not_change_the_test_outcome_exit_code(
    cli_env, selected_workspace, fake_newman, monkeypatch, capsys
):
    # A execução dos testes e a persistência são responsabilidades
    # distintas: mesmo se a gravação do result.json falhar, o exit code
    # continua refletindo o resultado real dos testes (sucesso aqui).
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    class _FailingRepository:
        def save(self, **_kwargs):
            raise OSError("disco cheio (simulado)")

    monkeypatch.setattr(bootstrap, "JsonExecutionResultRepository", lambda: _FailingRepository())

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS
    captured = capsys.readouterr()
    assert "Execution finished successfully." in captured.out
    assert "não foi possível salvar" in captured.err.lower()


def test_run_write_failure_with_test_failures_still_returns_functional_failure(
    cli_env, selected_workspace, fake_newman, monkeypatch, capsys
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "with_test_failures")

    class _FailingRepository:
        def save(self, **_kwargs):
            raise OSError("disco cheio (simulado)")

    monkeypatch.setattr(bootstrap, "JsonExecutionResultRepository", lambda: _FailingRepository())

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == FUNCTIONAL_FAILURE
    assert "não foi possível salvar" in capsys.readouterr().err.lower()


# --- run --file (modo offline) ----------------------------------------------------------------


def _write_local_collection(path: Path, *, name: str = "Local Collection") -> Path:
    document = {
        "info": {
            "name": name,
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            {
                "name": "Ping",
                "id": "req-1",
                "request": {"method": "GET", "url": "https://api.exemplo.com/ping"},
                "response": [{"name": "ok", "status": "OK", "code": 200, "header": [], "body": "{}"}],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_run_from_file_succeeds_without_api_key(offline_env, fake_newman_offline, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    collection_path = _write_local_collection(offline_env / "collection.json")

    exit_code = main(["run", "--file", str(collection_path)])

    assert exit_code == SUCCESS
    out = capsys.readouterr().out
    assert "Local Collection" in out
    assert "N/A (execução local)" in out
    assert "Execution finished successfully." in out


def test_run_from_file_with_test_failures_returns_functional_failure(
    offline_env, fake_newman_offline, monkeypatch
):
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "with_test_failures")
    collection_path = _write_local_collection(offline_env / "collection.json")

    exit_code = main(["run", "--file", str(collection_path)])

    assert exit_code == FUNCTIONAL_FAILURE


def test_run_from_file_with_executable_not_found_returns_integration_failure(offline_env):
    collection_path = _write_local_collection(offline_env / "collection.json")

    exit_code = main(
        ["run", "--file", str(collection_path), "--newman-executable", "newman-que-nao-existe"]
    )

    assert exit_code == INTEGRATION_FAILURE


def test_run_from_file_with_missing_collection_reports_invalid_input(offline_env):
    exit_code = main(["run", "--file", str(offline_env / "nao-existe.json")])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_run_from_file_never_requires_postman_api_key(offline_env, fake_newman_offline, monkeypatch):
    monkeypatch.delenv("POSTMAN_API_KEY", raising=False)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    collection_path = _write_local_collection(offline_env / "collection.json")

    exit_code = main(["run", "--file", str(collection_path)])

    assert exit_code == SUCCESS


def test_run_from_file_persists_result_with_null_workspace_and_collection_id(
    offline_env, fake_newman_offline, monkeypatch
):
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    collection_path = _write_local_collection(offline_env / "collection.json", name="PetStore Local")

    exit_code = main(["run", "--file", str(collection_path)])

    assert exit_code == SUCCESS
    matches = list(offline_env.glob("artifacts/run_*/result.json"))
    assert len(matches) == 1
    payload = json.loads(matches[0].read_text(encoding="utf-8"))
    assert payload["workspace"] == {"id": None, "name": None}
    assert payload["collection"] == {"id": None, "name": "PetStore Local"}


# --- run --file: exclusividade com seleção remota ----------------------------------------------------------------


def test_run_rejects_file_and_collection_id_together(offline_env):
    collection_path = _write_local_collection(offline_env / "collection.json")

    exit_code = main(["run", "--file", str(collection_path), "--collection-id", COLLECTION_A_ID])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_run_rejects_file_and_collection_name_together(offline_env):
    collection_path = _write_local_collection(offline_env / "collection.json")

    exit_code = main(
        ["run", "--file", str(collection_path), "--collection-name", COLLECTION_A_NAME]
    )

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_run_rejects_file_and_index_together(offline_env):
    collection_path = _write_local_collection(offline_env / "collection.json")

    exit_code = main(["run", "1", "--file", str(collection_path)])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


# --- run --file + report: mesmo pipeline funciona pra online e offline ----------------------------------------------------------------


def test_report_reads_a_result_produced_by_run_from_file(
    offline_env, fake_newman_offline, monkeypatch, capsys
):
    # report_command.py usa bootstrap.build_report_context() sem override de
    # caminho (JsonExecutionResultReader() usa "artifacts/" relativo ao CWD)
    # — muda o diretório de trabalho para o mesmo local onde offline_env já
    # isolou os artefatos de `run --file`, para os dois lados enxergarem o
    # mesmo result.json.
    monkeypatch.chdir(offline_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    collection_path = _write_local_collection(offline_env / "collection.json", name="Offline Run")

    run_exit_code = main(["run", "--file", str(collection_path)])
    assert run_exit_code == SUCCESS

    report_exit_code = main(["report"])

    assert report_exit_code == SUCCESS
    out = capsys.readouterr().out
    assert "Report generated successfully." in out


# --- --environment ----------------------------------------------------------------


def _write_environment_file(path: Path) -> Path:
    payload = {
        "values": [
            {"key": "base_url", "value": "https://api.exemplo.com", "type": "default"},
            {"key": "token", "value": "segredo-de-environment", "type": "secret"},
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_run_environment_flag_is_forwarded_without_breaking_execution(
    cli_env, selected_workspace, fake_newman, monkeypatch, tmp_path
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    environment_path = _write_environment_file(tmp_path / "environment_smoke.json")

    exit_code = main(
        ["run", "--collection-id", COLLECTION_A_ID, "--environment", str(environment_path)]
    )

    assert exit_code == SUCCESS


def test_run_from_file_environment_flag_is_forwarded_without_breaking_execution(
    offline_env, fake_newman_offline, monkeypatch
):
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")
    collection_path = _write_local_collection(offline_env / "collection.json")
    environment_path = _write_environment_file(offline_env / "environment.json")

    exit_code = main(
        ["run", "--file", str(collection_path), "--environment", str(environment_path)]
    )

    assert exit_code == SUCCESS


def test_run_environment_empty_value_reports_invalid_input(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID, "--environment", ""])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_run_without_environment_flag_still_defaults_to_none(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    # Regressão: sem --environment, o comportamento continua exatamente
    # como antes (environment_path=None).
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS


# --- run --target playwright (gap 2 da revisão P0) ----------------------------------------


def _make_suite_dir(base: Path) -> Path:
    suite_dir = base / "playwright_suite"
    suite_dir.mkdir()
    (suite_dir / "conftest.py").write_text("", encoding="utf-8")
    return suite_dir


def test_run_target_playwright_succeeds(offline_env, fake_pytest_offline, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    suite_dir = _make_suite_dir(offline_env)

    exit_code = main(["run", "--target", "playwright", "--file", str(suite_dir)])

    assert exit_code == SUCCESS
    out = capsys.readouterr().out
    assert "Execution finished successfully." in out
    assert "pytest" in out


def test_run_target_playwright_with_test_failures_returns_functional_failure(
    offline_env, fake_pytest_offline, monkeypatch
):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    suite_dir = _make_suite_dir(offline_env)

    exit_code = main(["run", "--target", "playwright", "--file", str(suite_dir)])

    assert exit_code == FUNCTIONAL_FAILURE


def test_run_target_playwright_requires_file(offline_env):
    exit_code = main(["run", "--target", "playwright"])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION


def test_run_target_playwright_missing_executable_returns_integration_failure_and_guidance(
    offline_env, capsys
):
    # Sem fake_pytest_offline: PlaywrightAdapter real apontado para um
    # executável que não existe — mesma prova (nenhum mock da lógica de
    # detecção) que test_run_with_executable_not_found... faz pro Newman.
    exit_code = main(
        [
            "run",
            "--target",
            "playwright",
            "--file",
            str(_make_suite_dir(offline_env)),
            "--pytest-executable",
            "pytest-que-nao-existe",
        ]
    )

    assert exit_code == INTEGRATION_FAILURE
    out = capsys.readouterr().out
    assert "infrastructure error" in out
    assert "pytest" in out.lower()


def test_run_target_playwright_executable_env_var_is_used_when_flag_absent(
    offline_env, fake_pytest_offline, monkeypatch
):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    monkeypatch.setenv("PYTEST_EXECUTABLE", "caminho-da-env")
    suite_dir = _make_suite_dir(offline_env)

    exit_code = main(["run", "--target", "playwright", "--file", str(suite_dir)])

    # fake_pytest_offline ignora o valor resolvido (sempre roda o fake real),
    # mas confirma que a resolução env var > default não quebra a execução —
    # mesma cobertura de precedência que os testes de --newman-executable já
    # fazem para o Newman.
    assert exit_code == SUCCESS


def test_run_target_playwright_never_touches_newman_output(
    offline_env, fake_pytest_offline, monkeypatch, capsys
):
    # Prova de que a mensagem de infraestrutura é a variante própria do
    # Playwright, não a do Newman (que fala "Newman execution failed").
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    exit_code = main(["run", "--target", "playwright", "--file", str(offline_env / "nao-existe")])

    assert exit_code == INTEGRATION_FAILURE
    out = capsys.readouterr().out
    assert "Newman" not in out
    assert "Playwright execution failed" in out


def test_run_target_playwright_summary_shows_skipped(
    offline_env, fake_pytest_offline, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "with_skipped")
    suite_dir = _make_suite_dir(offline_env)

    exit_code = main(["run", "--target", "playwright", "--file", str(suite_dir)])

    assert exit_code == FUNCTIONAL_FAILURE
    out = capsys.readouterr().out
    assert "Skipped: 1" in out


def test_run_target_playwright_no_tests_collected_returns_integration_failure(
    offline_env, fake_pytest_offline, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "no_tests_collected")
    suite_dir = _make_suite_dir(offline_env)

    exit_code = main(["run", "--target", "playwright", "--file", str(suite_dir)])

    assert exit_code == INTEGRATION_FAILURE
    out = capsys.readouterr().out
    assert "infrastructure error" in out


def test_run_target_playwright_persists_result_json(
    offline_env, fake_pytest_offline, monkeypatch
):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    suite_dir = _make_suite_dir(offline_env)

    exit_code = main(["run", "--target", "playwright", "--file", str(suite_dir)])

    assert exit_code == SUCCESS
    matches = list(offline_env.glob("artifacts/run_*/result.json"))
    assert len(matches) == 1
    payload = json.loads(matches[0].read_text(encoding="utf-8"))
    assert payload["success"] is True
    assert payload["collection"] == {"id": None, "name": "playwright_suite"}
    assert payload["workspace"] == {"id": None, "name": None}


def test_run_target_playwright_infrastructure_failure_does_not_persist_result_json(
    offline_env, fake_pytest_offline, monkeypatch
):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")

    exit_code = main(["run", "--target", "playwright", "--file", str(offline_env / "nao-existe")])

    assert exit_code == INTEGRATION_FAILURE
    assert list(offline_env.glob("artifacts/run_*/result.json")) == []


def test_run_target_playwright_never_requires_postman_api_key(
    offline_env, fake_pytest_offline, monkeypatch
):
    monkeypatch.delenv("POSTMAN_API_KEY", raising=False)
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    suite_dir = _make_suite_dir(offline_env)

    exit_code = main(["run", "--target", "playwright", "--file", str(suite_dir)])

    assert exit_code == SUCCESS


def test_run_target_postman_is_still_the_default(cli_env, selected_workspace, fake_newman, monkeypatch):
    # Regressão explícita: omitir --target continua se comportando
    # exatamente como antes desta flag existir.
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS


def test_run_target_postman_explicit_behaves_like_default(
    cli_env, selected_workspace, fake_newman, monkeypatch
):
    configure_server(cli_env)
    monkeypatch.setenv("FAKE_NEWMAN_MODE", "success")

    exit_code = main(["run", "--target", "postman", "--collection-id", COLLECTION_A_ID])

    assert exit_code == SUCCESS


# --- run --target playwright: mascaramento de secret via --environment ----------------------


def _write_environment_with_secret(path: Path, *, secret_value: str) -> Path:
    payload = {
        "values": [
            {"key": "base_url", "value": "https://api.exemplo.com", "type": "default"},
            {"key": "token", "value": secret_value, "type": "secret"},
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_run_target_playwright_masks_known_secret_in_test_failures(
    offline_env, fake_pytest_offline, monkeypatch, capsys
):
    secret_value = "sk_live_super_secret_token_playwright"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures_with_secret")
    monkeypatch.setenv("FAKE_PYTEST_SECRET_VALUE", secret_value)
    suite_dir = _make_suite_dir(offline_env)
    environment_path = _write_environment_with_secret(
        offline_env / "environment.json", secret_value=secret_value
    )

    exit_code = main(
        [
            "run",
            "--target",
            "playwright",
            "--file",
            str(suite_dir),
            "--environment",
            str(environment_path),
        ]
    )

    assert exit_code == FUNCTIONAL_FAILURE
    captured = capsys.readouterr()
    assert secret_value not in captured.out
    assert secret_value not in captured.err


def test_run_target_playwright_persisted_json_never_contains_secret(
    offline_env, fake_pytest_offline, monkeypatch
):
    secret_value = "sk_live_super_secret_token_playwright"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures_with_secret")
    monkeypatch.setenv("FAKE_PYTEST_SECRET_VALUE", secret_value)
    suite_dir = _make_suite_dir(offline_env)
    environment_path = _write_environment_with_secret(
        offline_env / "environment.json", secret_value=secret_value
    )

    main(
        [
            "run",
            "--target",
            "playwright",
            "--file",
            str(suite_dir),
            "--environment",
            str(environment_path),
        ]
    )

    matches = list(offline_env.glob("artifacts/run_*/result.json"))
    assert len(matches) == 1
    assert secret_value not in matches[0].read_text(encoding="utf-8")


def _read_persisted_result_json(base: Path) -> str:
    matches = list(base.glob("artifacts/run_*/result.json"))
    assert len(matches) == 1
    return matches[0].read_text(encoding="utf-8")


def test_run_target_playwright_never_masks_a_value_not_marked_secret(
    offline_env, fake_pytest_offline, monkeypatch
):
    # "não mascarar indiscriminadamente": um valor sem "type": "secret" no
    # Environment precisa continuar visível na evidência persistida
    # (result.json — onde o error_message da falha realmente é gravado; o
    # resumo impresso no console nunca chega a exibir o texto da falha).
    public_value = "valor-publico-sem-risco"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures_with_secret")
    monkeypatch.setenv("FAKE_PYTEST_SECRET_VALUE", public_value)
    suite_dir = _make_suite_dir(offline_env)
    environment_path = offline_env / "environment.json"
    environment_path.write_text(
        json.dumps({"values": [{"key": "base_url", "value": public_value, "type": "default"}]}),
        encoding="utf-8",
    )

    main(
        [
            "run",
            "--target",
            "playwright",
            "--file",
            str(suite_dir),
            "--environment",
            str(environment_path),
        ]
    )

    assert public_value in _read_persisted_result_json(offline_env)


def test_run_target_playwright_without_environment_never_masks_anything(
    offline_env, fake_pytest_offline, monkeypatch
):
    value = "aparece-normalmente-sem-environment"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures_with_secret")
    monkeypatch.setenv("FAKE_PYTEST_SECRET_VALUE", value)
    suite_dir = _make_suite_dir(offline_env)

    main(["run", "--target", "playwright", "--file", str(suite_dir)])

    assert value in _read_persisted_result_json(offline_env)


def test_run_target_playwright_malformed_environment_never_blocks_execution(
    offline_env, fake_pytest_offline, monkeypatch
):
    # Mascaramento é best-effort: um Environment inválido nunca deveria
    # impedir a execução em si.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    suite_dir = _make_suite_dir(offline_env)
    environment_path = offline_env / "environment.json"
    environment_path.write_text("isto não é um json válido", encoding="utf-8")

    exit_code = main(
        [
            "run",
            "--target",
            "playwright",
            "--file",
            str(suite_dir),
            "--environment",
            str(environment_path),
        ]
    )

    assert exit_code == SUCCESS


def test_run_target_playwright_environment_empty_value_reports_invalid_input(
    offline_env, fake_pytest_offline, monkeypatch
):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    suite_dir = _make_suite_dir(offline_env)

    exit_code = main(
        ["run", "--target", "playwright", "--file", str(suite_dir), "--environment", ""]
    )

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION
