import json
import os
import sys
import time
import uuid
import zipfile


def _extract_report_path(argv: list) -> str | None:
    for index, arg in enumerate(argv):
        if arg == "--junitxml" and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _maybe_write_http_transactions() -> None:
    # P1.2: simula o que o api_context gerado escreveria — controlado por
    # FAKE_PYTEST_TRANSACTIONS (lista de dicts em JSON), independente do
    # FAKE_PYTEST_MODE, pra testar o parsing/masking do PlaywrightAdapter
    # sem depender do playwright real. Ausente = nenhuma chamada HTTP
    # simulada (igual a uma suíte sem nenhum api_context.get/post/...).
    #
    # FAKE_PYTEST_RAW_TRANSACTIONS (texto verbatim, sem passar por
    # json.loads) existe à parte só pra testar o adapter lendo uma linha
    # NDJSON estruturalmente inválida — nunca usado nos demais cenários.
    transactions_path = os.environ.get("PLAYWRIGHT_HTTP_TRANSACTIONS_PATH")
    if not transactions_path:
        return

    raw_verbatim = os.environ.get("FAKE_PYTEST_RAW_TRANSACTIONS")
    if raw_verbatim is not None:
        with open(transactions_path, "a", encoding="utf-8") as handle:
            handle.write(raw_verbatim)
        return

    raw_transactions = os.environ.get("FAKE_PYTEST_TRANSACTIONS")
    if not raw_transactions:
        return
    transactions = json.loads(raw_transactions)
    with open(transactions_path, "a", encoding="utf-8") as handle:
        handle.writelines(
            json.dumps(entry, ensure_ascii=False) + "\n" for entry in transactions
        )


def _maybe_write_assertion_results() -> None:
    # P1.1 (detalhamento de assertions): mesmo raciocínio de
    # _maybe_write_http_transactions, para o NDJSON de resultado de
    # assertion (name/expected/actual/status/precision/reason) — controlado
    # por FAKE_PYTEST_ASSERTION_RESULTS (lista de dicts em JSON) ou
    # FAKE_PYTEST_RAW_ASSERTION_RESULTS (texto verbatim, pra testar linha
    # corrompida).
    results_path = os.environ.get("PLAYWRIGHT_ASSERTION_RESULTS_PATH")
    if not results_path:
        return

    raw_verbatim = os.environ.get("FAKE_PYTEST_RAW_ASSERTION_RESULTS")
    if raw_verbatim is not None:
        with open(results_path, "a", encoding="utf-8") as handle:
            handle.write(raw_verbatim)
        return

    raw_results = os.environ.get("FAKE_PYTEST_ASSERTION_RESULTS")
    if not raw_results:
        return
    results = json.loads(raw_results)
    with open(results_path, "a", encoding="utf-8") as handle:
        handle.writelines(json.dumps(entry, ensure_ascii=False) + "\n" for entry in results)


def _maybe_write_trace_artifacts() -> None:
    # P1.3 (Trace em falha): simula o que a fixture api_context gerada
    # escreveria para um teste que falhou — controlado por
    # FAKE_PYTEST_TRACE_ARTIFACTS (lista de {"test_id":, "files": {nome do
    # membro do zip -> conteúdo texto}}), independente do FAKE_PYTEST_MODE.
    # Um .zip DE VERDADE é criado dentro de PLAYWRIGHT_TRACE_DIR (nunca só
    # simulado), pra exercitar o mask_trace_archive/zipfile real do
    # PlaywrightAdapter, exatamente como um trace real do Playwright seria
    # lido depois.
    #
    # FAKE_PYTEST_RAW_TRACE_MANIFEST (texto verbatim) existe à parte só
    # pra testar o adapter lendo uma linha de manifesto estruturalmente
    # inválida — nunca usado nos demais cenários.
    manifest_path = os.environ.get("PLAYWRIGHT_TRACE_ARTIFACTS_PATH")
    trace_dir = os.environ.get("PLAYWRIGHT_TRACE_DIR")
    if not manifest_path or not trace_dir:
        return

    raw_verbatim = os.environ.get("FAKE_PYTEST_RAW_TRACE_MANIFEST")
    if raw_verbatim is not None:
        with open(manifest_path, "a", encoding="utf-8") as handle:
            handle.write(raw_verbatim)
        return

    raw_artifacts = os.environ.get("FAKE_PYTEST_TRACE_ARTIFACTS")
    if not raw_artifacts:
        return
    artifacts = json.loads(raw_artifacts)
    manifest_lines = []
    for artifact in artifacts:
        # P1.5 (infrastructure failure das evidências): "error" simula uma
        # falha de CAPTURA/FINALIZAÇÃO do Trace em si (tracing.stop()
        # levantando, ver _finish_trace no conftest.py gerado) — nenhum
        # .zip é criado, mesma forma de entrada do manifesto que o
        # conftest.py real escreveria nesse caso.
        error_type = artifact.get("error")
        if error_type:
            manifest_lines.append(
                json.dumps(
                    {"test_id": artifact["test_id"], "error": error_type}, ensure_ascii=False
                )
            )
            continue
        zip_path = os.path.join(trace_dir, uuid.uuid4().hex + ".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for member_name, content in artifact.get("files", {}).items():
                zip_file.writestr(member_name, content)
        manifest_lines.append(
            json.dumps({"test_id": artifact["test_id"], "path": zip_path}, ensure_ascii=False)
        )
    with open(manifest_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(manifest_lines) + ("\n" if manifest_lines else ""))


def main() -> int:
    mode = os.environ.get("FAKE_PYTEST_MODE", "success")
    report_path = _extract_report_path(sys.argv)
    _maybe_write_http_transactions()
    _maybe_write_assertion_results()
    _maybe_write_trace_artifacts()

    if mode == "slow":
        time.sleep(float(os.environ.get("FAKE_PYTEST_SLEEP_SECONDS", "5")))

    if mode == "stderr_only":
        # Simula uma falha antes de o pytest conseguir escrever o relatório:
        # nenhum arquivo é gerado no caminho de export.
        sys.stderr.write("erro simulado do pytest no stderr\n")
        return 2

    if mode == "crash_no_output":
        # Nenhum stdout/stderr/arquivo — simula um crash abrupto do processo.
        return 3

    if mode == "invalid_report":
        # O pytest real também pode deixar um arquivo corrompido/incompleto
        # no caminho de export em caso de falha durante a escrita.
        if report_path:
            with open(report_path, "w", encoding="utf-8") as handle:
                handle.write("isto não é um XML válido")
        return 1

    if mode == "empty_report":
        if report_path:
            open(report_path, "w", encoding="utf-8").close()
        return 1

    if mode == "no_tests_collected":
        # pytest real: diretório válido, processo roda normalmente, mas
        # nenhuma função test_* foi encontrada — exit code 5
        # (pytest.ExitCode.NO_TESTS_COLLECTED), relatório com tests="0".
        if report_path:
            with open(report_path, "w", encoding="utf-8") as handle:
                handle.write(
                    '<testsuites><testsuite name="pytest" tests="0" failures="0" '
                    'errors="0" skipped="0" time="0.01"></testsuite></testsuites>'
                )
        sys.stdout.write("no tests ran\n")
        return 5

    report_xml = _build_report(mode)

    if mode == "stdout_decoy":
        # Prova de que o stdout nunca é tratado como fonte do relatório: aqui
        # ele contém um trecho de XML "chamariz", propositalmente diferente
        # do relatório real gravado no arquivo de export.
        sys.stdout.write('<testsuite tests="999" failures="0" errors="0" skipped="0"></testsuite>')
    elif mode == "test_failures_with_secret":
        # Prova de que o adapter mascara o stdout, não só a error_message —
        # o valor secret aparece cru aqui, como se tivesse vazado por um
        # print/log de dentro do teste real.
        secret_value = os.environ.get("FAKE_PYTEST_SECRET_VALUE", "")
        sys.stdout.write(f"FAILED endpoints/test_login.py - token vazado: {secret_value}\n")
    else:
        # pytest real: stdout traz só mensagens de progresso da execução,
        # nunca o relatório JUnit em si (esse vai exclusivamente para o
        # arquivo de --junitxml).
        sys.stdout.write("===== test session starts =====\n")

    if report_path:
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(report_xml)

    exit_codes = {
        "success": 0,
        "stdout_decoy": 0,
        "test_failures": 1,
        "with_skipped": 1,
        "with_errors": 1,
        "test_failures_with_secret": 1,
    }
    return exit_codes.get(mode, 1)


def _build_report(mode: str) -> str:
    if mode in ("success", "stdout_decoy"):
        return (
            '<testsuites><testsuite name="pytest" tests="2" failures="0" errors="0" '
            'skipped="0" time="0.5">'
            '<testcase classname="endpoints.test_get_users" name="test_get_users_success" '
            'time="0.2" />'
            '<testcase classname="endpoints.test_post_users" name="test_post_users_success" '
            'time="0.3" />'
            "</testsuite></testsuites>"
        )

    if mode == "test_failures":
        return (
            '<testsuites><testsuite name="pytest" tests="2" failures="1" errors="0" '
            'skipped="0" time="0.5">'
            '<testcase classname="endpoints.test_get_users" name="test_get_users_success" '
            'time="0.2" />'
            '<testcase classname="endpoints.test_post_users" name="test_post_users_success" '
            'time="0.3">'
            '<failure message="assert 500 == 201">Traceback completo aqui</failure>'
            "</testcase>"
            "</testsuite></testsuites>"
        )

    if mode == "with_skipped":
        return (
            '<testsuites><testsuite name="pytest" tests="3" failures="1" errors="0" '
            'skipped="1" time="0.7">'
            '<testcase classname="endpoints.test_get_users" name="test_get_users_success" '
            'time="0.2" />'
            '<testcase classname="endpoints.test_post_users" name="test_post_users_success" '
            'time="0.3">'
            '<failure message="assert 500 == 201">Traceback completo aqui</failure>'
            "</testcase>"
            '<testcase classname="endpoints.test_trace_users" name="test_placeholder" '
            'time="0.0">'
            '<skipped message="Geração de asserções Playwright ainda não implementada." />'
            "</testcase>"
            "</testsuite></testsuites>"
        )

    if mode == "test_failures_with_secret":
        secret_value = os.environ.get("FAKE_PYTEST_SECRET_VALUE", "")
        message = f"expected response body to not contain token {secret_value}"
        return (
            '<testsuites><testsuite name="pytest" tests="1" failures="1" errors="0" '
            'skipped="0" time="0.1">'
            '<testcase classname="endpoints.test_login" name="test_login_success" time="0.1">'
            f'<failure message="{message}">Traceback completo aqui</failure>'
            "</testcase>"
            "</testsuite></testsuites>"
        )

    if mode == "with_errors":
        return (
            '<testsuites><testsuite name="pytest" tests="1" failures="0" errors="1" '
            'skipped="0" time="0.1">'
            '<testcase classname="endpoints.test_get_users" name="test_get_users_success" '
            'time="0.1">'
            '<error message="fixture &#x27;api_context&#x27; not found">'
            "Traceback completo aqui</error>"
            "</testcase>"
            "</testsuite></testsuites>"
        )

    # Qualquer outro modo (ex.: "slow", usado só para medir tempo decorrido
    # real) cai aqui: mesmo relatório de "test_failures", igual ao critério
    # já usado por tests/fake_newman.py (só success/stdout_decoy têm relatório
    # sem falha; qualquer outro modo é tratado como "execução com reprovação",
    # nunca um erro de execução do fake em si).
    return _build_report("test_failures")


if __name__ == "__main__":
    sys.exit(main())
