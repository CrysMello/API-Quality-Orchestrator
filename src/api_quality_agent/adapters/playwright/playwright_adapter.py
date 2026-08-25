import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from api_quality_agent.domain.exceptions import InputError
from api_quality_agent.domain.models import (
    ExecutionResult,
    InfrastructureFailure,
    InfrastructureFailureType,
    TestFailure,
)
from api_quality_agent.domain.policies import ensure_non_empty_id
from api_quality_agent.ports.outbound.collection_runner import DEFAULT_RUN_TIMEOUT_SECONDS
from api_quality_agent.shared import mask_all_occurrences

# P0.1: equivalente ao NewmanAdapter para a suíte Playwright já gerada por
# este projeto (PlaywrightEndpointTestGenerator/DefaultPlaywrightTestSuiteBuilder)
# — mesmo pipeline de execução (subprocess -> relatório exportado em arquivo
# -> ExecutionResult), nunca um segundo pipeline paralelo. Reaproveita
# integralmente ExecutionResult/InfrastructureFailure/TestFailure do domínio;
# nenhum tipo "PlaywrightExecutionResult" próprio.
DEFAULT_PYTEST_EXECUTABLE = "pytest"
_JUNIT_REPORT_FILENAME = "pytest-junit-report.xml"
# pytest.ExitCode.NO_TESTS_COLLECTED (biblioteca padrão do pytest) — valor
# estável e documentado pelo próprio pytest, nunca reaproveitado para outro
# significado.
_PYTEST_EXIT_CODE_NO_TESTS_COLLECTED = 5


class _JunitReportNotGeneratedError(Exception):
    pass


class _InvalidJunitReportError(Exception):
    pass


class PlaywrightAdapter:
    def __init__(
        self,
        *,
        pytest_executable: str = DEFAULT_PYTEST_EXECUTABLE,
        command_prefix: tuple[str, ...] = (),
    ) -> None:
        # command_prefix existe só para permitir testes com um executável de
        # substituição real (nunca o pytest de verdade — ver
        # tests/fake_pytest.py), sem depender de shell nem de mocks. Mesmo
        # padrão do NewmanAdapter.
        ensure_non_empty_id(pytest_executable, "pytest_executable")
        self._executable = pytest_executable
        self._command_prefix = command_prefix

    def run(
        self,
        *,
        tests_path: str,
        timeout_seconds: float = DEFAULT_RUN_TIMEOUT_SECONDS,
        known_secret_values: tuple[str, ...] = (),
    ) -> ExecutionResult:
        # known_secret_values: valores já conhecidos como secret por quem
        # chama (ex.: run_command.py, lendo EnvironmentVariable.is_secret via
        # PostmanEnvironmentParser) — o adapter nunca decide sozinho o que é
        # secret (não há convenção de nome AQO_* que distinga secret de
        # variável comum), só aplica o mascaramento sobre uma lista já
        # resolvida. Extensível: uma fonte futura de segredo só precisa
        # contribuir mais valores para esta mesma tupla.
        ensure_non_empty_id(tests_path, "tests_path")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise InputError("timeout_seconds deve ser um número maior que zero.")

        start = time.monotonic()

        # Validação local e determinística do caminho da suíte, antes de
        # sequer iniciar o processo: evita depender de heurísticas sobre a
        # saída/exit code do pytest (ex.: exit code 4 de "usage error") para
        # saber se o caminho informado era inválido. Mesmo espírito de
        # NewmanAdapter._validate_collection_file.
        if not Path(tests_path).is_dir():
            return _infrastructure_result(
                tests_path,
                InfrastructureFailureType.TEST_SUITE_NOT_FOUND,
                f"Caminho da suíte Playwright não encontrado ou não é um diretório: "
                f"{tests_path!r}.",
                duration=time.monotonic() - start,
            )

        # O relatório oficial é o arquivo exportado via --junitxml (nativo do
        # pytest, sem plugin/dependência extra), nunca o stdout do processo —
        # mesmo raciocínio do NewmanAdapter com --reporter-json-export.
        report_dir = tempfile.mkdtemp(prefix="api-quality-agent-pytest-")
        report_path = str(Path(report_dir) / _JUNIT_REPORT_FILENAME)
        try:
            return self._run_with_report_export(
                tests_path=tests_path,
                timeout_seconds=timeout_seconds,
                report_path=report_path,
                start=start,
                known_secret_values=known_secret_values,
            )
        finally:
            shutil.rmtree(report_dir, ignore_errors=True)

    def _run_with_report_export(
        self,
        *,
        tests_path: str,
        timeout_seconds: float,
        report_path: str,
        start: float,
        known_secret_values: tuple[str, ...],
    ) -> ExecutionResult:
        argv = [
            self._executable,
            *self._command_prefix,
            tests_path,
            "--junitxml",
            report_path,
        ]

        try:
            completed = subprocess.run(  # noqa: S603 - argv explícito, shell=False
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
            )
        except FileNotFoundError:
            return _infrastructure_result(
                tests_path,
                InfrastructureFailureType.EXECUTABLE_NOT_FOUND,
                f"Executável do pytest não encontrado: {self._executable!r}. "
                "Verifique se o pytest está instalado e no PATH, ou configure "
                "o caminho explícito do executável.",
                duration=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired as exc:
            return _infrastructure_result(
                tests_path,
                InfrastructureFailureType.TIMEOUT,
                f"Execução do pytest excedeu o tempo limite de {timeout_seconds}s.",
                duration=time.monotonic() - start,
                stdout=mask_all_occurrences(_decode(exc.stdout), known_secret_values),
                stderr=mask_all_occurrences(_decode(exc.stderr), known_secret_values),
            )
        except OSError as exc:
            return _infrastructure_result(
                tests_path,
                InfrastructureFailureType.UNEXPECTED_ERROR,
                f"Falha inesperada ao iniciar o processo do pytest: {exc}",
                duration=time.monotonic() - start,
            )

        duration = time.monotonic() - start
        # stdout/stderr nunca são a fonte do relatório — servem só para
        # diagnóstico (mensagens do CLI, avisos, erros de execução). Sempre
        # mascarados aqui, antes de alimentar qualquer um dos caminhos de
        # retorno abaixo — nunca um valor secret cru chega ao chamador.
        stdout = mask_all_occurrences(completed.stdout or "", known_secret_values)
        stderr = mask_all_occurrences(completed.stderr or "", known_secret_values)

        if completed.returncode == _PYTEST_EXIT_CODE_NO_TESTS_COLLECTED:
            # "Diferenciar: nenhum teste coletado" — nunca reportado como um
            # total_requests=0/success=false comum: o diretório era válido e
            # o processo completou, mas nada foi encontrado pra rodar (ex.:
            # pasta sem nenhum test_*.py). Exit code original preservado.
            return _infrastructure_result(
                tests_path,
                InfrastructureFailureType.NO_TESTS_COLLECTED,
                "O pytest não coletou nenhum teste no caminho informado "
                f"({tests_path!r}); confirme se a suíte foi gerada corretamente.",
                duration=duration,
                stdout=stdout,
                stderr=stderr,
                exit_code=completed.returncode,
            )

        try:
            total, failed, skipped, failures = _read_report_file(report_path)
        except (_JunitReportNotGeneratedError, _InvalidJunitReportError) as exc:
            return ExecutionResult(
                collection_source=tests_path,
                success=False,
                exit_code=completed.returncode,
                duration_seconds=duration,
                total_requests=0,
                total_assertions=0,
                failed_assertions=0,
                skipped_tests=0,
                test_failures=(),
                infrastructure_failure=InfrastructureFailure(
                    failure_type=InfrastructureFailureType.UNEXPECTED_ERROR,
                    message=str(exc),
                ),
                stdout=stdout,
                stderr=stderr,
            )

        failures = tuple(
            TestFailure(
                request_name=failure.request_name,
                test_name=failure.test_name,
                error_message=mask_all_occurrences(failure.error_message, known_secret_values),
            )
            for failure in failures
        )

        return ExecutionResult(
            # Reaproveita collection_source para guardar o caminho da suíte
            # executada — ExecutionResult é o mesmo tipo do NewmanAdapter, e
            # este campo já representa genericamente "o que foi executado".
            collection_source=tests_path,
            success=completed.returncode == 0,
            exit_code=completed.returncode,
            duration_seconds=duration,
            # total_requests e total_assertions recebem o MESMO número: o
            # JUnit do pytest só sabe contar "testes" (uma função de teste),
            # sem a granularidade de "requisição" vs. "assertion" que o
            # relatório do Newman tem (pm.test por assertion). É uma
            # aproximação honesta da granularidade disponível, não uma
            # distinção inventada.
            total_requests=total,
            total_assertions=total,
            failed_assertions=failed,
            skipped_tests=skipped,
            test_failures=failures,
            infrastructure_failure=None,
            stdout=stdout,
            stderr=stderr,
        )


def _infrastructure_result(
    tests_path: str,
    failure_type: InfrastructureFailureType,
    message: str,
    *,
    duration: float,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
) -> ExecutionResult:
    # exit_code=None é o caso comum (nenhum processo chegou a rodar de
    # verdade: caminho inválido, executável ausente); NO_TESTS_COLLECTED é a
    # única chamada que informa um exit_code real (5), preservando-o como
    # pedido — nunca descartado silenciosamente.
    return ExecutionResult(
        collection_source=tests_path,
        success=False,
        exit_code=exit_code,
        duration_seconds=duration,
        total_requests=0,
        total_assertions=0,
        failed_assertions=0,
        skipped_tests=0,
        test_failures=(),
        infrastructure_failure=InfrastructureFailure(failure_type=failure_type, message=message),
        stdout=stdout,
        stderr=stderr,
    )


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _read_report_file(report_path: str) -> tuple[int, int, int, tuple[TestFailure, ...]]:
    path = Path(report_path)
    if not path.exists():
        raise _JunitReportNotGeneratedError(
            "O pytest não gerou o arquivo de relatório esperado; o processo "
            "pode ter falhado antes de concluir a execução (consulte stdout/"
            "stderr para diagnóstico)."
        )

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _JunitReportNotGeneratedError(
            f"Não foi possível ler o arquivo de relatório do pytest: {exc}"
        ) from exc

    if not raw_text.strip():
        raise _InvalidJunitReportError("O arquivo de relatório do pytest está vazio.")

    try:
        root = ET.fromstring(raw_text)  # noqa: S314 - arquivo próprio, gerado localmente
    except ET.ParseError as exc:
        raise _InvalidJunitReportError(
            f"Relatório do pytest não é um XML válido: {exc}"
        ) from exc

    return _parse_junit_root(root)


def _parse_junit_root(root: ET.Element) -> tuple[int, int, int, tuple[TestFailure, ...]]:
    # pytest (junit_family=xunit2, default desde o pytest 6) exporta
    # <testsuites><testsuite ...>...</testsuite></testsuites>; versões/
    # configurações mais antigas podem exportar <testsuite> como raiz direto
    # — aceita as duas formas, nunca assume uma só.
    testsuite = root if root.tag == "testsuite" else root.find("testsuite")
    if testsuite is None:
        raise _InvalidJunitReportError(
            "Relatório do pytest não possui a estrutura esperada ('testsuite' ausente)."
        )

    total = _safe_int(testsuite.get("tests"))
    # "errors" (falha de setup/fixture/coleta) e "failures" (assertion) são
    # categorias distintas no JUnit do pytest, mas para este domínio (que só
    # distingue "sucesso" de "falha", nunca as duas categorias do pytest)
    # ambas contam como falha — nada é descartado silenciosamente.
    failed = _safe_int(testsuite.get("errors")) + _safe_int(testsuite.get("failures"))
    skipped = _safe_int(testsuite.get("skipped"))

    failures: list[TestFailure] = []
    for testcase in testsuite.findall("testcase"):
        failure_element = testcase.find("failure")
        if failure_element is None:
            failure_element = testcase.find("error")
        if failure_element is None:
            continue

        # request_name vem do classname do JUnit (caminho do módulo do teste,
        # ex.: "endpoints.test_get_users") — não do nome de negócio "Request:
        # X" da docstring do .py gerado, que o relatório JUnit não expõe.
        request_name = testcase.get("classname")
        test_name = testcase.get("name") or "desconhecido"
        message = (
            failure_element.get("message")
            or (failure_element.text or "").strip()
            or "Falha sem mensagem."
        )
        failures.append(
            TestFailure(request_name=request_name, test_name=test_name, error_message=message)
        )

    return total, failed, skipped, tuple(failures)


def _safe_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0
