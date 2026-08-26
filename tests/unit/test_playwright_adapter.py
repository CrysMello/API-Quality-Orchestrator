"""P0.1: PlaywrightAdapter — equivalente ao NewmanAdapter para a suíte
Playwright já gerada por este projeto (pytest via subprocess, relatório lido
do arquivo --junitxml exportado, nunca do stdout). Mesma cobertura de cenários
do NewmanAdapter (tests/unit/test_newman_adapter.py), adaptada às diferenças
reais entre os dois motores (skipped, errors vs. failures do JUnit, ausência
de arquivo de environment).
"""

import json
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


# --- P1.2: captura estruturada de transação HTTP -----------------------------------------


def _set_transactions(monkeypatch, transactions: list[dict]) -> None:
    monkeypatch.setenv("FAKE_PYTEST_TRANSACTIONS", json.dumps(transactions))


def _get_transaction(**overrides) -> dict:
    defaults = {
        "method": "GET",
        "url": "https://api.exemplo.com/users",
        "request_headers": {"Accept": "application/json"},
        "request_body": None,
        "response_status": 200,
        "response_headers": {"content-type": "application/json"},
        "response_body": '{"items": []}',
    }
    defaults.update(overrides)
    return defaults


def test_captures_a_get_call(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(monkeypatch, [_get_transaction()])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert len(result.http_transactions) == 1
    transaction = result.http_transactions[0]
    assert transaction.method == "GET"
    assert transaction.url == "https://api.exemplo.com/users"
    assert transaction.request_body is None


def test_captures_the_test_id_of_the_transaction(tmp_path, monkeypatch):
    # P1.1: correlação test_id -> request/response depende de
    # HttpTransaction.test_id ser lido do NDJSON — não só existir no
    # domínio.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(monkeypatch, [_get_transaction(test_id="test_get_users_success")])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.http_transactions[0].test_id == "test_get_users_success"


def test_captures_a_post_call_with_body(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(
        monkeypatch,
        [
            _get_transaction(
                method="POST",
                url="https://api.exemplo.com/users",
                request_body={"name": "Maria", "active": True},
                response_status=201,
                response_body='{"id": 1, "name": "Maria"}',
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    transaction = result.http_transactions[0]
    assert transaction.method == "POST"
    assert transaction.request_body is not None
    assert "Maria" in transaction.request_body
    assert "true" in transaction.request_body.lower()


def test_captures_the_response(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(
        monkeypatch, [_get_transaction(response_status=200, response_body='{"items": [1, 2]}')]
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    transaction = result.http_transactions[0]
    assert transaction.response_status == 200
    assert transaction.response_body == '{"items": [1, 2]}'


def test_captures_request_and_response_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(
        monkeypatch,
        [
            _get_transaction(
                request_headers={"Accept": "application/json", "X-Trace": "abc"},
                response_headers={"content-type": "application/json", "x-request-id": "req-1"},
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    transaction = result.http_transactions[0]
    request_header_names = {header.name for header in transaction.request_headers}
    response_header_names = {header.name for header in transaction.response_headers}
    assert request_header_names == {"Accept", "X-Trace"}
    assert response_header_names == {"content-type", "x-request-id"}
    assert (
        next(h.value for h in transaction.request_headers if h.name == "X-Trace") == "abc"
    )


def test_captures_the_status_code(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(monkeypatch, [_get_transaction(response_status=404)])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.http_transactions[0].response_status == 404


def test_absence_of_request_body_is_preserved_as_none(tmp_path, monkeypatch):
    # GET/DELETE/HEAD sem data/form/multipart no call site — nunca inventa
    # um body vazio "{}" no lugar de None.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(monkeypatch, [_get_transaction(method="DELETE", request_body=None)])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.http_transactions[0].request_body is None


def test_captures_multiple_http_calls_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(
        monkeypatch,
        [
            _get_transaction(method="GET", url="https://api.exemplo.com/users"),
            _get_transaction(
                method="POST",
                url="https://api.exemplo.com/users",
                request_body={"name": "Maria"},
                response_status=201,
            ),
            _get_transaction(method="DELETE", url="https://api.exemplo.com/users/1", request_body=None),
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert len(result.http_transactions) == 3
    assert [t.method for t in result.http_transactions] == ["GET", "POST", "DELETE"]


def test_no_transactions_file_means_no_http_transactions(tmp_path, monkeypatch):
    # Sem FAKE_PYTEST_TRANSACTIONS: o fake não escreve o arquivo — mesmo
    # cenário de uma suíte real sem nenhuma chamada HTTP feita.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.http_transactions == ()


# --- P1.2: masking de secret na evidência de transação HTTP ------------------------------


def test_known_secret_is_masked_in_request_headers_and_body(tmp_path, monkeypatch):
    secret_value = "sk_live_super_secret_token_123456"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(
        monkeypatch,
        [
            _get_transaction(
                method="POST",
                request_headers={"Authorization": f"Bearer {secret_value}"},
                request_body={"password": secret_value},
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    transaction = result.http_transactions[0]
    assert secret_value not in transaction.request_body
    assert all(secret_value not in header.value for header in transaction.request_headers)
    # mascarado, não removido — prefixo/sufixo do valor original continuam
    # visíveis, prova de que não foi um apagamento silencioso.
    auth_header = next(h for h in transaction.request_headers if h.name == "Authorization")
    assert "****" in auth_header.value


def test_known_secret_is_masked_in_response_headers_and_body(tmp_path, monkeypatch):
    secret_value = "sk_live_super_secret_token_123456"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(
        monkeypatch,
        [
            _get_transaction(
                response_headers={"X-Session-Token": secret_value},
                response_body=json.dumps({"token": secret_value}),
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    transaction = result.http_transactions[0]
    assert secret_value not in transaction.response_body
    assert all(secret_value not in header.value for header in transaction.response_headers)


def test_known_secret_is_masked_in_the_url(tmp_path, monkeypatch):
    secret_value = "sk_live_super_secret_token_123456"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(
        monkeypatch,
        [_get_transaction(url=f"https://api.exemplo.com/users?token={secret_value}")],
    )
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    assert secret_value not in result.http_transactions[0].url


def test_value_not_in_known_secret_values_is_never_masked_in_transactions(tmp_path, monkeypatch):
    # "não mascarar indiscriminadamente": um valor que não está na lista de
    # secrets conhecidos continua visível na evidência.
    public_value = "customer-id-nao-e-secret"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(monkeypatch, [_get_transaction(request_body={"customerId": public_value})])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path), known_secret_values=())

    assert public_value in result.http_transactions[0].request_body


def test_malformed_transaction_line_is_skipped_without_breaking_the_run(tmp_path, monkeypatch):
    # Robustez: uma linha NDJSON corrompida (ex.: escrita interrompida por
    # um crash no meio da suíte) nunca derruba a execução nem as demais
    # transações válidas na mesma linha por linha.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    valid_entry = json.dumps(_get_transaction(method="GET"))
    monkeypatch.setenv(
        "FAKE_PYTEST_RAW_TRANSACTIONS",
        f"{valid_entry}\nisto não é uma linha JSON válida\n",
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.success is True  # a linha corrompida nunca vira infrastructure_failure
    assert len(result.http_transactions) == 1
    assert result.http_transactions[0].method == "GET"


def test_transaction_entry_missing_required_field_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    incomplete_entry = json.dumps({"method": "GET"})  # sem url/response_status etc.
    valid_entry = json.dumps(_get_transaction(method="POST"))
    monkeypatch.setenv(
        "FAKE_PYTEST_RAW_TRANSACTIONS", f"{incomplete_entry}\n{valid_entry}\n"
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert len(result.http_transactions) == 1
    assert result.http_transactions[0].method == "POST"


# --- P1.1 (detalhamento de assertions): captura de assertion_results ------------------------


def _set_assertion_results(monkeypatch, results: list[dict]) -> None:
    monkeypatch.setenv("FAKE_PYTEST_ASSERTION_RESULTS", json.dumps(results))


def _assertion_result_entry(**overrides) -> dict:
    defaults = {
        "test_id": "test_post_users_success",
        "name": "HTTP status",
        "expected": 201,
        "actual": 201,
        "status": "PASSED",
        "precision": "EXACT",
        "reason": "Status HTTP 201 documentado explicitamente para este cenário "
        "(evidência: contract).",
    }
    defaults.update(overrides)
    return defaults


def test_http_status_passing_is_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(monkeypatch, [_assertion_result_entry()])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert len(result.assertion_results) == 1
    entry = result.assertion_results[0]
    assert entry.name == "HTTP status"
    assert entry.expected == 201
    assert entry.actual == 201
    assert entry.status == "PASSED"


def test_http_status_failing_is_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                expected=201,
                actual=500,
                status="FAILED",
                reason="Status HTTP 201 documentado explicitamente para este cenário "
                "(evidência: contract).",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    entry = result.assertion_results[0]
    assert entry.status == "FAILED"
    assert entry.expected == 201
    assert entry.actual == 500
    # expected e actual diferentes — exatamente o que se espera de uma falha.
    assert entry.expected != entry.actual


def test_required_field_present_is_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                name="required_field:id",
                expected="presente",
                actual="presente",
                status="PASSED",
                reason="1 campo(s) declarado(s) como 'required' no schema documentado "
                "(evidência: contract).",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    entry = result.assertion_results[0]
    assert entry.name == "required_field:id"
    assert entry.expected == "presente"
    assert entry.actual == "presente"
    assert entry.status == "PASSED"


def test_required_field_absent_is_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                name="required_field:id",
                expected="presente",
                actual="ausente",
                status="FAILED",
                reason="1 campo(s) declarado(s) como 'required' no schema documentado "
                "(evidência: contract).",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    entry = result.assertion_results[0]
    assert entry.expected == "presente"
    assert entry.actual == "ausente"
    assert entry.status == "FAILED"


def test_schema_valid_is_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                name="json_schema",
                expected="válido conforme schema documentado",
                actual="válido",
                status="PASSED",
                precision="EXACT",
                reason="Validação estrutural completa contra o schema documentado "
                "(biblioteca jsonschema; evidência: contract).",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    entry = result.assertion_results[0]
    assert entry.name == "json_schema"
    assert entry.status == "PASSED"
    assert entry.actual == "válido"


def test_schema_invalid_is_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                name="json_schema",
                expected="válido conforme schema documentado",
                actual="inválido: 'id' is a required property",
                status="FAILED",
                precision="EXACT",
                reason="Validação estrutural completa contra o schema documentado "
                "(biblioteca jsonschema; evidência: contract).",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    entry = result.assertion_results[0]
    assert entry.status == "FAILED"
    assert "required property" in entry.actual


def test_precision_exact_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(monkeypatch, [_assertion_result_entry(precision="EXACT")])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.assertion_results[0].precision == "EXACT"


def test_precision_derived_is_preserved(tmp_path, monkeypatch):
    # DERIVED existe hoje na classificação de expected_values (enum de 2+
    # valores) — não instrumentado ainda com _record_assertion_result nesta
    # parte (ver limitações), então este teste valida o pipeline de
    # captura/persistência/masking em si, não uma chamada gerada de verdade.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                name="expected_values",
                expected=("active", "inactive", "pending"),
                actual="active",
                status="PASSED",
                precision="DERIVED",
                reason="2 campo(s) com conjunto de valores permitidos derivado do 'enum' "
                "documentado no schema, sem valor único garantido.",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.assertion_results[0].precision == "DERIVED"


def test_precision_broad_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                name="HTTP status",
                expected="resposta presente",
                actual="presente",
                status="PASSED",
                precision="BROAD",
                reason="Nenhuma evidência de status disponível (estratégia de teste, "
                "Postman, OpenAPI, contrato ou exemplo); mantida a validação aproximada "
                "de que a resposta existe.",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.assertion_results[0].precision == "BROAD"


def test_reason_reflects_a_real_source(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(
        monkeypatch,
        [_assertion_result_entry(reason="Status HTTP 201 documentado explicitamente para "
        "este cenário (evidência: contract).")],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    # A razão sempre aponta a fonte real (evidência: contract) — nunca um
    # texto genérico tipo "porque sim".
    assert "evidência: contract" in result.assertion_results[0].reason


def test_absence_of_source_never_fabricates_a_reason(tmp_path, monkeypatch):
    # BROAD por ausência total de evidência: reason explica a AUSÊNCIA,
    # nunca inventa uma justificativa como se houvesse fonte.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                precision="BROAD",
                reason="Nenhuma evidência de status disponível (estratégia de teste, "
                "Postman, OpenAPI, contrato ou exemplo); mantida a validação aproximada "
                "de que a resposta existe.",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    reason = result.assertion_results[0].reason
    assert "Nenhuma evidência" in reason
    assert "evidência: contract" not in reason


def test_multiple_assertion_results_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(name="HTTP status"),
            _assertion_result_entry(name="required_field:id"),
            _assertion_result_entry(name="json_schema"),
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert [r.name for r in result.assertion_results] == [
        "HTTP status",
        "required_field:id",
        "json_schema",
    ]


def test_no_assertion_results_file_means_empty_tuple(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.assertion_results == ()


def test_malformed_assertion_result_line_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    valid_entry = json.dumps(_assertion_result_entry())
    monkeypatch.setenv(
        "FAKE_PYTEST_RAW_ASSERTION_RESULTS", f"{valid_entry}\nisto não é json\n"
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.success is True
    assert len(result.assertion_results) == 1


# --- Correlação test_id -> request/response -> assertions -----------------------------------


def test_test_id_correlates_transaction_and_assertion(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_transactions(
        monkeypatch,
        [
            _get_transaction(
                method="POST",
                url="https://api.exemplo.com/users",
                test_id="test_post_users_success",
            )
        ],
    )
    _set_assertion_results(monkeypatch, [_assertion_result_entry()])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.http_transactions[0].test_id == result.assertion_results[0].test_id
    assert result.assertion_results[0].test_id == "test_post_users_success"


# --- Masking de secret em assertion_results --------------------------------------------------


def test_known_secret_is_masked_in_actual(tmp_path, monkeypatch):
    secret_value = "sk_live_super_secret_token_123456"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                name="json_schema",
                actual=f"inválido: token esperado {secret_value}",
                status="FAILED",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    assert secret_value not in str(result.assertion_results[0].actual)


def test_known_secret_is_masked_in_reason(tmp_path, monkeypatch):
    secret_value = "sk_live_super_secret_token_123456"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(
        monkeypatch,
        [_assertion_result_entry(reason=f"Valor documentado no contrato: {secret_value}.")],
    )
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    assert secret_value not in result.assertion_results[0].reason


def test_no_known_secret_leaks_in_any_assertion_result_field(tmp_path, monkeypatch):
    secret_value = "sk_live_super_secret_token_123456"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_assertion_results(
        monkeypatch,
        [
            _assertion_result_entry(
                name=f"field:{secret_value}",
                expected=secret_value,
                actual=f"inválido: {secret_value}",
                reason=f"Documentado no contrato como {secret_value}.",
            )
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    entry = result.assertion_results[0]
    assert secret_value not in entry.name
    assert secret_value not in str(entry.expected)
    assert secret_value not in str(entry.actual)
    assert secret_value not in entry.reason


def test_value_not_in_known_secret_values_is_never_masked_in_assertion_results(
    tmp_path, monkeypatch
):
    public_value = "active"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    _set_assertion_results(monkeypatch, [_assertion_result_entry(actual=public_value)])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path), known_secret_values=())

    assert result.assertion_results[0].actual == public_value


# --- P1.3 (Trace em falha): captura de trace_artifacts --------------------------------------


def _set_trace_artifacts(monkeypatch, artifacts: list[dict]) -> None:
    monkeypatch.setenv("FAKE_PYTEST_TRACE_ARTIFACTS", json.dumps(artifacts))


def _trace_files(**overrides: str) -> dict:
    # Mesmos 3 membros de texto que um trace real (snapshots=True,
    # sources=False) sempre tem — ver trace_masking.py. "trace.stacks" é
    # sempre um blob mínimo válido; os outros dois, NDJSON com uma linha
    # cada, mesmo formato real observado empiricamente contra o
    # Playwright instalado neste projeto.
    defaults = {
        "trace.trace": (
            json.dumps(
                {
                    "type": "before",
                    "callId": "call@1",
                    "class": "APIRequestContext",
                    "method": "fetch",
                    "params": {
                        "url": "/users",
                        "method": "POST",
                        "headers": [
                            {"name": "Authorization", "value": "Bearer sk_live_super_secret"},
                        ],
                        "postData": "eyJwYXNzd29yZCI6ICJodW50ZXIyIn0=",
                    },
                }
            )
            + "\n"
        ),
        "trace.network": (
            json.dumps(
                {
                    "type": "resource-snapshot",
                    "snapshot": {
                        "request": {
                            "method": "POST",
                            "url": "http://127.0.0.1/users",
                            "headers": [
                                {"name": "Cookie", "value": "session=abc123secret"},
                            ],
                        },
                        "response": {"status": 201},
                    },
                }
            )
            + "\n"
        ),
        "trace.stacks": json.dumps({"files": [], "stacks": []}),
    }
    defaults.update(overrides)
    return defaults


def _trace_artifact_entry(test_id: str = "test_post_users_fail", **file_overrides: str) -> dict:
    return {"test_id": test_id, "files": _trace_files(**file_overrides)}


def _read_trace_zip_texts(path: str) -> dict:
    import zipfile

    with zipfile.ZipFile(path) as zip_file:
        return {name: zip_file.read(name).decode("utf-8") for name in zip_file.namelist()}


def test_passing_test_never_persists_a_trace(tmp_path, monkeypatch):
    # Regra explícita do bloco: PASS -> nenhum trace, nunca um artefato
    # desnecessário. FAKE_PYTEST_TRACE_ARTIFACTS nem é setado aqui — mesmo
    # comportamento de uma suíte real onde a fixture nunca chama
    # tracing.stop(path=...) para um teste que passou.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.trace_artifacts == ()


def test_failing_test_persists_a_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_trace_artifacts(monkeypatch, [_trace_artifact_entry()])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert len(result.trace_artifacts) == 1
    artifact = result.trace_artifacts[0]
    assert artifact.type == "playwright-trace"
    assert artifact.test_id == "test_post_users_fail"
    assert Path(artifact.path).is_file()
    assert artifact.path.endswith(".zip")


def test_trace_is_associated_with_the_correct_test_id(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_trace_artifacts(monkeypatch, [_trace_artifact_entry(test_id="test_delete_user_fail")])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.trace_artifacts[0].test_id == "test_delete_user_fail"


def test_multiple_failing_tests_produce_separate_trace_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_trace_artifacts(
        monkeypatch,
        [
            _trace_artifact_entry(test_id="test_a_fail"),
            _trace_artifact_entry(test_id="test_b_fail"),
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert len(result.trace_artifacts) == 2
    test_ids = {artifact.test_id for artifact in result.trace_artifacts}
    assert test_ids == {"test_a_fail", "test_b_fail"}
    paths = {artifact.path for artifact in result.trace_artifacts}
    assert len(paths) == 2  # arquivos distintos, nunca o mesmo .zip reaproveitado


def test_only_the_failing_test_generates_a_trace_artifact(tmp_path, monkeypatch):
    # Mesmo cenário de "with_skipped" (2 test cases reais no relatório,
    # um deles falho) — só o manifesto simulado para o teste que falhou
    # existe, nunca inventado para o que passou.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_trace_artifacts(monkeypatch, [_trace_artifact_entry(test_id="test_post_users_fail")])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert [a.test_id for a in result.trace_artifacts] == ["test_post_users_fail"]


def test_no_trace_manifest_means_empty_trace_artifacts(tmp_path, monkeypatch):
    # Suíte gerada antes da P1.3 (conftest.py sem a instrumentação de
    # trace) nunca seta PLAYWRIGHT_TRACE_ARTIFACTS_PATH com conteúdo —
    # compatibilidade: nunca um erro, só "nada pra reportar".
    monkeypatch.setenv("FAKE_PYTEST_MODE", "success")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.trace_artifacts == ()


def test_malformed_trace_manifest_line_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    monkeypatch.setenv("FAKE_PYTEST_RAW_TRACE_MANIFEST", "isto não é um JSON válido\n")
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.trace_artifacts == ()


def test_trace_referencing_a_missing_raw_file_is_skipped(tmp_path, monkeypatch):
    # Manifesto aponta para um caminho que nunca foi de fato escrito (ex.:
    # processo morto no meio do tracing.stop) — nunca inventa um artefato.
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    monkeypatch.setenv(
        "FAKE_PYTEST_RAW_TRACE_MANIFEST",
        json.dumps({"test_id": "test_x", "path": str(tmp_path / "nunca-existiu.zip")}) + "\n",
    )
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path))

    assert result.trace_artifacts == ()


# --- P1.3: masking do conteúdo do trace (nunca o mesmo mecanismo de --------
# --- known_secret_values sozinho — ver trace_masking.py) -------------------


def test_known_secret_is_masked_inside_the_persisted_trace(tmp_path, monkeypatch):
    secret_value = "sk_live_super_secret"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_trace_artifacts(monkeypatch, [_trace_artifact_entry()])
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    texts = _read_trace_zip_texts(result.trace_artifacts[0].path)
    for text in texts.values():
        assert secret_value not in text


def test_authorization_and_cookie_headers_are_always_redacted_in_the_trace(tmp_path, monkeypatch):
    # Redação estrutural por NOME de header — nunca depende de o valor
    # estar em known_secret_values (aqui, deliberadamente, nenhum secret é
    # informado ao adapter).
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_trace_artifacts(monkeypatch, [_trace_artifact_entry()])
    adapter = _build_adapter()

    result = adapter.run(tests_path=_minimal_suite_dir(tmp_path), known_secret_values=())

    texts = _read_trace_zip_texts(result.trace_artifacts[0].path)
    assert "sk_live_super_secret" not in texts["trace.trace"]
    assert "abc123secret" not in texts["trace.network"]
    assert "[REDACTED]" in texts["trace.trace"]
    assert "[REDACTED]" in texts["trace.network"]


def test_resource_body_inside_the_trace_is_masked(tmp_path, monkeypatch):
    # resources/*.bin é o corpo real do request/response no formato do
    # Playwright — a captura estruturada por nome de header não alcança
    # este membro (não é trace.trace/trace.network/trace.stacks), mas o
    # masking por known_secret_values ainda se aplica (ver
    # trace_masking._mask_resource_member). O caso de um resource
    # genuinamente binário (não-UTF-8) é coberto em unidade dedicada em
    # test_trace_masking.py, sem depender do fake pytest.
    secret_value = "hunter2"
    monkeypatch.setenv("FAKE_PYTEST_MODE", "test_failures")
    _set_trace_artifacts(
        monkeypatch,
        [
            {
                "test_id": "test_post_users_fail",
                "files": {
                    **_trace_files(),
                    "resources/body.bin": json.dumps({"password": secret_value}),
                },
            }
        ],
    )
    adapter = _build_adapter()

    result = adapter.run(
        tests_path=_minimal_suite_dir(tmp_path), known_secret_values=(secret_value,)
    )

    texts = _read_trace_zip_texts(result.trace_artifacts[0].path)
    assert secret_value not in texts["resources/body.bin"]
