"""P0.1: PlaywrightAdapter — equivalente ao NewmanAdapter para a suíte
Playwright já gerada por este projeto (pytest via subprocess, relatório lido
do arquivo --junitxml exportado, nunca do stdout). Mesma cobertura de cenários
do NewmanAdapter (tests/unit/test_newman_adapter.py), adaptada às diferenças
reais entre os dois motores (skipped, errors vs. failures do JUnit, ausência
de arquivo de environment).
"""

import sys
import tempfile
from pathlib import Path

import pytest

from api_quality_agent.adapters.playwright import PlaywrightAdapter
from api_quality_agent.domain.exceptions import InputError
from api_quality_agent.domain.models import InfrastructureFailureType

FAKE_PYTEST_SCRIPT = Path(__file__).resolve().parent.parent / "fake_pytest.py"


def _build_adapter(**overrides) -> PlaywrightAdapter:
    params = {
        "pytest_executable": sys.executable,
        "command_prefix": (str(FAKE_PYTEST_SCRIPT),),
    }
    params.update(overrides)
    return PlaywrightAdapter(**params)


def _minimal_suite_dir(tmp_path: Path) -> str:
    suite_dir = tmp_path / "playwright_suite"
    suite_dir.mkdir()
    (suite_dir / "conftest.py").write_text("", encoding="utf-8")
    return str(suite_dir)


# --- Sucesso -----------------------------------------------------------------------------


def test_success_run_returns_successful_result(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.success is True
    assert result.exit_code == 0
    assert result.infrastructure_failure is None
    assert result.test_failures == ()
    assert result.total_requests == 2
    assert result.total_assertions == 2
    assert result.failed_assertions == 0
    assert result.skipped_tests == 0
    assert result.duration_seconds >= 0.0


# --- Testes reprovados ---------------------------------------------------------------------


def test_failed_tests_are_captured_without_infrastructure_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.success is False
    assert result.infrastructure_failure is None  # reprovação de teste não é falha de infra
    assert len(result.test_failures) == 1
    failure = result.test_failures[0]
    assert failure.request_name == "endpoints.test_post_users"
    assert failure.test_name == "test_post_users_success"
    assert "assert 500 == 201" in failure.error_message


# --- Skipped (conceito que o Newman não tem) ------------------------------------------------


def test_skipped_tests_are_captured_separately_from_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "with_skipped")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.total_requests == 3
    assert result.skipped_tests == 1
    assert result.failed_assertions == 1
    # o skip em si nunca vira um TestFailure — só failure/error contam como
    # falha; um teste puramente skipped não é uma reprovação.
    assert len(result.test_failures) == 1
    assert result.test_failures[0].test_name == "test_post_users_success"


# --- Erros de fixture/coleta (<error> do JUnit) --------------------------------------------


def test_pytest_collection_or_fixture_errors_are_folded_into_failed_assertions(
    tmp_path, monkeypatch
):
    # "errors" (setup/fixture/coleta) e "failures" (assertion) são categorias
    # distintas no JUnit do pytest; este domínio só tem sucesso/falha, então
    # ambas contam como falha — nada é descartado silenciosamente.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "with_errors")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.success is False
    assert result.failed_assertions == 1
    assert len(result.test_failures) == 1
    assert "api_context" in result.test_failures[0].error_message


# --- Executável ausente ---------------------------------------------------------------------


def test_missing_executable_returns_infrastructure_failure(tmp_path):
    adapter = PlaywrightAdapter(pytest_executable="este-executavel-nao-existe-xyz")

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.success is False
    assert result.exit_code is None
    assert result.infrastructure_failure is not None
    assert result.infrastructure_failure.failure_type == InfrastructureFailureType.EXECUTABLE_NOT_FOUND


# --- Timeout -----------------------------------------------------------------------------


def test_timeout_returns_infrastructure_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "slow")
    monkeypatch.setenv("FAKE_PYTEST_SLEEP_SECONDS", "5")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path), timeout_seconds=0.3)

    assert result.success is False
    assert result.exit_code is None
    assert result.infrastructure_failure is not None
    assert result.infrastructure_failure.failure_type == InfrastructureFailureType.TIMEOUT


# --- stderr ------------------------------------------------------------------------------


def test_stderr_is_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "stderr_only")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert "erro simulado do pytest no stderr" in result.stderr


# --- Caminho da suíte inválido -------------------------------------------------------------


def test_missing_tests_path_is_detected_before_spawning_process(tmp_path):
    # executável propositalmente inexistente: se o processo fosse mesmo
    # iniciado, o resultado seria EXECUTABLE_NOT_FOUND, não TEST_SUITE_NOT_FOUND
    adapter = PlaywrightAdapter(pytest_executable="este-executavel-nao-existe-xyz")

    result = adapter.run(tests_path=str(tmp_path / "nao-existe"))

    assert result.infrastructure_failure is not None
    assert result.infrastructure_failure.failure_type == InfrastructureFailureType.TEST_SUITE_NOT_FOUND


def test_tests_path_that_is_a_file_is_detected_before_spawning_process(tmp_path):
    file_path = tmp_path / "isto-e-um-arquivo.py"
    file_path.write_text("", encoding="utf-8")
    adapter = PlaywrightAdapter(pytest_executable="este-executavel-nao-existe-xyz")

    result = adapter.run(tests_path=str(file_path))

    assert result.infrastructure_failure is not None
    assert result.infrastructure_failure.failure_type == InfrastructureFailureType.TEST_SUITE_NOT_FOUND


# --- Falha de infraestrutura genérica (relatório ilegível) ---------------------------------


def test_unparsable_report_is_reported_as_infrastructure_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "invalid_report")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.infrastructure_failure is not None
    assert result.infrastructure_failure.failure_type == InfrastructureFailureType.UNEXPECTED_ERROR


# --- Argumentos --------------------------------------------------------------------------


def test_rejects_empty_tests_path():
    adapter = _build_adapter()

    with pytest.raises(InputError):
        adapter.run(tests_path="")


def test_rejects_non_positive_timeout(tmp_path):
    adapter = _build_adapter()

    with pytest.raises(InputError):
        adapter.run(tests_path=_minimal_suite_dir(tmp_path), timeout_seconds=0)


def test_rejects_empty_pytest_executable():
    with pytest.raises(InputError):
        PlaywrightAdapter(pytest_executable="")


# --- Processo simulado (subprocess real, nunca o pytest de verdade) ------------------------


def test_uses_a_real_subprocess_with_real_elapsed_time(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "slow")
    monkeypatch.setenv("FAKE_PYTEST_SLEEP_SECONDS", "0.2")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path), timeout_seconds=5)

    assert result.duration_seconds >= 0.15
    assert result.infrastructure_failure is None  # completou normalmente, após o sleep real


# --- Relatório vem do arquivo exportado, nunca do stdout ------------------------------------


def test_report_is_read_from_export_file_not_from_stdout(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "stdout_decoy")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    # O stdout contém um trecho "chamariz" com tests=999; o resultado precisa
    # vir do arquivo exportado (tests=2) — prova de que o stdout nunca é
    # tratado como fonte do relatório.
    assert result.total_requests == 2
    assert result.success is True


def test_fake_pytest_does_not_print_report_xml_to_stdout(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert "<testsuite" not in result.stdout


# --- Ausência do relatório / arquivo vazio ---------------------------------------------------


def test_missing_report_file_is_reported_as_infrastructure_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "crash_no_output")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.infrastructure_failure is not None
    assert result.infrastructure_failure.failure_type == InfrastructureFailureType.UNEXPECTED_ERROR
    assert "não gerou" in result.infrastructure_failure.message.lower()


def test_empty_report_file_is_reported_as_infrastructure_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "empty_report")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.infrastructure_failure is not None
    assert result.infrastructure_failure.failure_type == InfrastructureFailureType.UNEXPECTED_ERROR
    assert "vazio" in result.infrastructure_failure.message.lower()


# --- Exit code diferente de zero, com stdout/stderr preservados -----------------------------


def test_non_zero_exit_code_is_preserved_alongside_stdout_and_stderr(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.exit_code == 1
    assert result.infrastructure_failure is None
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)


# --- Limpeza do diretório temporário ---------------------------------------------------------


def test_temporary_report_directory_is_removed_after_success(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    created_dirs: list[str] = []
    original_mkdtemp = tempfile.mkdtemp

    def _tracking_mkdtemp(*args, **kwargs):
        path = original_mkdtemp(*args, **kwargs)
        created_dirs.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", _tracking_mkdtemp)
    adapter = _build_adapter()

    adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists()


def test_temporary_report_directory_is_removed_even_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "invalid_report")
    created_dirs: list[str] = []
    original_mkdtemp = tempfile.mkdtemp

    def _tracking_mkdtemp(*args, **kwargs):
        path = original_mkdtemp(*args, **kwargs)
        created_dirs.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", _tracking_mkdtemp)
    adapter = _build_adapter()

    adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists()


# --- Caminho com espaços -----------------------------------------------------------------------


def test_works_with_tests_path_containing_spaces(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    spaced_dir = tmp_path / "pasta com espaços"
    spaced_dir.mkdir()
    adapter = _build_adapter()

    result = adapter.run(tests_path=str(spaced_dir))

    assert result.success is True


# --- Aproximações documentadas (granularidade do JUnit) --------------------------------------


def test_total_requests_and_total_assertions_are_equal_given_junit_granularity(
    tmp_path, monkeypatch
):
    # JUnit não distingue "requisição" de "assertion" como o relatório do
    # Newman distingue — os dois campos recebem o mesmo número de testes.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.total_requests == result.total_assertions


# --- Gap 1: mascaramento de secret (known_secret_values) ------------------------------------


def test_known_secret_value_is_masked_in_stdout_and_failure_message(tmp_path, monkeypatch):
    secret_value = "sk_live_super_secret_token_123456"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures_with_secret")
    monkeypatch.setenv("FAKE_PYTEST_SECRET_VALUE", secret_value)
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    assert secret_value not in result.stdout
    assert secret_value not in result.stderr
    assert all(secret_value not in failure.error_message for failure in result.test_failures)
    # o valor mascarado (prefixo/sufixo visíveis) ainda aparece, provando que
    # a mensagem não foi simplesmente apagada.
    assert any("****" in failure.error_message for failure in result.test_failures)


def test_value_not_in_known_secret_values_is_never_masked(tmp_path, monkeypatch):
    # "não mascarar indiscriminadamente": um valor que apareça na saída mas
    # NÃO esteja em known_secret_values precisa continuar visível — o
    # adapter nunca decide sozinho o que é secret.
    public_value = "valor-publico-sem-risco"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures_with_secret")
    monkeypatch.setenv("FAKE_PYTEST_SECRET_VALUE", public_value)
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path), known_secret_values=())

    assert any(public_value in failure.error_message for failure in result.test_failures)
    assert public_value in result.stdout


def test_masking_without_known_secret_values_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.success is True  # known_secret_values=() (default) nunca quebra a execução


# --- Gap 3: pytest exit code 5 (nenhum teste coletado) ---------------------------------------


def test_no_tests_collected_is_a_distinct_infrastructure_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "no_tests_collected")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.success is False
    assert result.infrastructure_failure is not None
    assert result.infrastructure_failure.failure_type == InfrastructureFailureType.NO_TESTS_COLLECTED
    # exit code original preservado, não descartado como nos outros casos de
    # infraestrutura (executável ausente/timeout), onde nenhum processo real
    # chegou a rodar.
    assert result.exit_code == 5


def test_no_tests_collected_is_distinguishable_from_test_suite_not_found(tmp_path, monkeypatch):
    # "Diferenciar: nenhum teste coletado" vs. caminho inválido — os dois são
    # InfrastructureFailure, mas com failure_type e exit_code diferentes.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "no_tests_collected")
    adapter = _build_adapter()

    no_tests_result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))
    missing_path_result = adapter.run(tests_path=str(tmp_path / "nao-existe"))

    assert (
        no_tests_result.infrastructure_failure.failure_type
        != missing_path_result.infrastructure_failure.failure_type
    )
    assert no_tests_result.exit_code == 5
    assert missing_path_result.exit_code is None


def test_no_tests_collected_never_reported_as_zero_successful_tests(tmp_path, monkeypatch):
    # Requisito explícito: nunca "total_tests=0, success=false" silencioso —
    # sempre uma infrastructure_failure explícita e identificável.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "no_tests_collected")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.infrastructure_failure is not None


# --- Gap 3: as quatro classificações continuam distinguíveis entre si -----------------------


def test_the_four_outcomes_are_all_distinguishable(tmp_path, monkeypatch):
    adapter = _build_adapter()
    suite_dir = _minimal_suite_dir(tmp_path)

    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    successful = adapter.run(tests_path=suite_dir)

    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    with_test_failures = adapter.run(tests_path=suite_dir)

    monkeypatch.setenv("FAKE_PYTEST_MODE", "no_tests_collected")
    no_tests_collected = adapter.run(tests_path=suite_dir)

    infra_error = adapter.run(tests_path=str(tmp_path / "nao-existe"))

    # execução bem-sucedida
    assert successful.success is True
    assert successful.infrastructure_failure is None

    # testes executados com falha
    assert with_test_failures.success is False
    assert with_test_failures.infrastructure_failure is None
    assert len(with_test_failures.test_failures) >= 1

    # nenhum teste coletado
    assert no_tests_collected.infrastructure_failure is not None
    assert (
        no_tests_collected.infrastructure_failure.failure_type
        == InfrastructureFailureType.NO_TESTS_COLLECTED
    )

    # erro de infraestrutura/processo (caminho inválido, processo nem rodou)
    assert infra_error.infrastructure_failure is not None
    assert (
        infra_error.infrastructure_failure.failure_type
        == InfrastructureFailureType.TEST_SUITE_NOT_FOUND
    )
