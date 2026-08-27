import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from api_quality_agent.adapters.playwright.trace_masking import mask_trace_archive
from api_quality_agent.domain.exceptions import InputError
from api_quality_agent.domain.models import (
    AssertionResult,
    ExecutionResult,
    HttpTransaction,
    HttpTransactionHeader,
    InfrastructureFailure,
    InfrastructureFailureType,
    TestFailure,
    TraceArtifact,
)
from api_quality_agent.domain.policies import ensure_non_empty_id
from api_quality_agent.ports.outbound.collection_runner import DEFAULT_RUN_TIMEOUT_SECONDS
from api_quality_agent.shared import (
    ASSERTION_RESULTS_PATH_ENV_VAR,
    HTTP_TRANSACTIONS_PATH_ENV_VAR,
    TRACE_ARTIFACTS_PATH_ENV_VAR,
    TRACE_DIR_ENV_VAR,
    mask_all_occurrences,
)

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
# P1.2: arquivo NDJSON (uma transação HTTP por linha) que o api_context do
# conftest.py gerado escreve, via HTTP_TRANSACTIONS_PATH_ENV_VAR — mesma
# pasta temporária do relatório JUnit, removida no fim do run() igual a ele.
_HTTP_TRANSACTIONS_FILENAME = "http-transactions.ndjson"
# P1.1 (detalhamento de assertions): arquivo NDJSON (uma assertion
# realmente checada por linha) que o helper _record_assertion_result
# embutido em cada arquivo de teste gerado escreve, via
# ASSERTION_RESULTS_PATH_ENV_VAR — mesma pasta temporária, mesmo raciocínio.
_ASSERTION_RESULTS_FILENAME = "assertion-results.ndjson"
# P1.3 (Trace em falha): diretório (dentro da MESMA pasta temporária acima,
# removida no fim do run() junto com o resto) onde a fixture api_context
# grava o .zip BRUTO (ainda não mascarado) de cada teste que falhou, e
# arquivo NDJSON que correlaciona test_id -> esse caminho bruto — nunca o
# nome do arquivo sozinho como mecanismo de correlação.
_TRACE_RAW_DIR_NAME = "traces-raw"
_TRACE_MANIFEST_FILENAME = "trace-manifest.ndjson"


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
        # mesmo raciocínio do NewmanAdapter com --reporter-json-export. A
        # captura de transação HTTP (P1.2) segue o MESMO raciocínio: outro
        # arquivo na mesma pasta temporária, nunca stdout.
        report_dir = tempfile.mkdtemp(prefix="api-quality-agent-pytest-")
        report_path = str(Path(report_dir) / _JUNIT_REPORT_FILENAME)
        transactions_path = str(Path(report_dir) / _HTTP_TRANSACTIONS_FILENAME)
        assertion_results_path = str(Path(report_dir) / _ASSERTION_RESULTS_FILENAME)
        # traces-raw/ e o manifesto ficam DENTRO de report_dir (removidos no
        # fim junto com o resto) — só os .zip já MASCARADOS (ver
        # _read_trace_artifacts) precisam sobreviver além deste método, numa
        # pasta temporária própria cuja posse passa para quem for persistir
        # o ExecutionResult (PersistExecutionResultUseCase).
        trace_raw_dir = Path(report_dir) / _TRACE_RAW_DIR_NAME
        trace_raw_dir.mkdir(parents=True, exist_ok=True)
        trace_manifest_path = str(Path(report_dir) / _TRACE_MANIFEST_FILENAME)
        try:
            return self._run_with_report_export(
                tests_path=tests_path,
                timeout_seconds=timeout_seconds,
                report_path=report_path,
                transactions_path=transactions_path,
                assertion_results_path=assertion_results_path,
                trace_dir=str(trace_raw_dir),
                trace_manifest_path=trace_manifest_path,
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
        transactions_path: str,
        assertion_results_path: str,
        trace_dir: str,
        trace_manifest_path: str,
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
        # HTTP_TRANSACTIONS_PATH_ENV_VAR/ASSERTION_RESULTS_PATH_ENV_VAR/
        # TRACE_*_ENV_VAR nunca substituem o ambiente herdado (AQO_*, PATH
        # etc.) — só acrescentam variáveis de wiring interno, nunca uma
        # credencial. O código gerado lê essas variáveis pra saber onde
        # registrar cada transação HTTP (P1.2) / resultado de assertion
        # (P1.1) / trace de falha (P1.3); ausentes, a captura correspondente
        # fica desligada sem erro (ver _render_conftest e
        # _RECORD_ASSERTION_RESULT_SOURCE).
        subprocess_env = {
            **os.environ,
            HTTP_TRANSACTIONS_PATH_ENV_VAR: transactions_path,
            ASSERTION_RESULTS_PATH_ENV_VAR: assertion_results_path,
            TRACE_DIR_ENV_VAR: trace_dir,
            TRACE_ARTIFACTS_PATH_ENV_VAR: trace_manifest_path,
        }

        try:
            completed = subprocess.run(  # noqa: S603 - argv explícito, shell=False
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
                env=subprocess_env,
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
            # O processo chegou a rodar (e pode ter feito chamadas HTTP/
            # checado assertions antes do timeout) — evidência parcial é
            # lida do mesmo jeito que uma execução completa, nunca
            # descartada só porque o processo não terminou a tempo. Um
            # trace em andamento no momento do timeout nunca chega a ser
            # finalizado pelo `finally` da fixture (o processo é morto
            # abruptamente) — o manifesto simplesmente não tem entrada para
            # ele, nunca um arquivo incompleto/corrompido é interpretado
            # como trace válido.
            trace_artifacts, evidence_failures = _read_trace_artifacts(
                trace_manifest_path, known_secret_values
            )
            return _infrastructure_result(
                tests_path,
                InfrastructureFailureType.TIMEOUT,
                f"Execução do pytest excedeu o tempo limite de {timeout_seconds}s.",
                duration=time.monotonic() - start,
                stdout=mask_all_occurrences(_decode(exc.stdout), known_secret_values),
                stderr=mask_all_occurrences(_decode(exc.stderr), known_secret_values),
                http_transactions=_read_http_transactions(transactions_path, known_secret_values),
                assertion_results=_read_assertion_results(
                    assertion_results_path, known_secret_values
                ),
                trace_artifacts=trace_artifacts,
                evidence_failures=evidence_failures,
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
        http_transactions = _read_http_transactions(transactions_path, known_secret_values)
        assertion_results = _read_assertion_results(assertion_results_path, known_secret_values)
        trace_artifacts, evidence_failures = _read_trace_artifacts(
            trace_manifest_path, known_secret_values
        )

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
                http_transactions=http_transactions,
                assertion_results=assertion_results,
                trace_artifacts=trace_artifacts,
                evidence_failures=evidence_failures,
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
                http_transactions=http_transactions,
                assertion_results=assertion_results,
                trace_artifacts=trace_artifacts,
                evidence_failures=evidence_failures,
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
            http_transactions=http_transactions,
            assertion_results=assertion_results,
            trace_artifacts=trace_artifacts,
            evidence_failures=evidence_failures,
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
    http_transactions: tuple[HttpTransaction, ...] = (),
    assertion_results: tuple[AssertionResult, ...] = (),
    trace_artifacts: tuple[TraceArtifact, ...] = (),
    evidence_failures: tuple[InfrastructureFailure, ...] = (),
) -> ExecutionResult:
    # exit_code=None é o caso comum (nenhum processo chegou a rodar de
    # verdade: caminho inválido, executável ausente); NO_TESTS_COLLECTED é a
    # única chamada que informa um exit_code real (5), preservando-o como
    # pedido — nunca descartado silenciosamente. http_transactions=()/
    # assertion_results=()/trace_artifacts=()/evidence_failures=() são o
    # caso comum pelo mesmo motivo (processo nunca rodou de verdade);
    # TIMEOUT e NO_TESTS_COLLECTED passam evidência real quando ela existe.
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
        http_transactions=http_transactions,
        assertion_results=assertion_results,
        trace_artifacts=trace_artifacts,
        evidence_failures=evidence_failures,
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


# --- P1.2: transações HTTP (NDJSON escrito pelo api_context gerado) ---------


def _read_http_transactions(
    transactions_path: str, known_secret_values: tuple[str, ...]
) -> tuple[HttpTransaction, ...]:
    # Arquivo ausente (nenhuma chamada HTTP feita, ou suíte gerada antes da
    # P1.2, sem a captura no conftest.py) nunca é erro — só significa
    # "nenhuma transação pra reportar", igual a um relatório JUnit sem
    # falhas.
    path = Path(transactions_path)
    if not path.exists():
        return ()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return ()

    transactions: list[HttpTransaction] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            # Uma linha corrompida (ex.: escrita interrompida por um crash
            # no meio da suíte) nunca invalida as demais nem o resultado da
            # execução — evidência é best-effort, nunca um requisito
            # funcional do run em si.
            continue
        transaction = _parse_transaction_entry(entry, known_secret_values)
        if transaction is not None:
            transactions.append(transaction)
    return tuple(transactions)


def _parse_transaction_entry(
    entry: object, known_secret_values: tuple[str, ...]
) -> HttpTransaction | None:
    if not isinstance(entry, dict):
        return None
    try:
        return HttpTransaction(
            # test_id nunca carrega dado do usuário (é sempre o nome da
            # função de teste, conhecido em tempo de geração) — nunca
            # precisa de mascaramento, mesmo critério de
            # _parse_assertion_result_entry.
            test_id=str(entry.get("test_id") or ""),
            method=str(entry["method"]),
            url=mask_all_occurrences(str(entry["url"]), known_secret_values),
            request_headers=_masked_headers(entry.get("request_headers"), known_secret_values),
            request_body=_masked_body(entry.get("request_body"), known_secret_values),
            # P2.1 (evidência HTTP): mesmo mecanismo de masking de
            # request_headers/response_headers (_masked_headers já cobre o
            # formato nome/valor) — nenhuma segunda implementação de
            # masking introduzida.
            query_parameters=_masked_headers(
                entry.get("query_parameters"), known_secret_values
            ),
            response_status=int(entry["response_status"]),
            response_headers=_masked_headers(entry.get("response_headers"), known_secret_values),
            response_body=_masked_body(entry.get("response_body"), known_secret_values),
        )
    except (KeyError, TypeError, ValueError):
        # Entrada estruturalmente inválida (campo obrigatório ausente/tipo
        # errado) — descartada, nunca inventada, mesmo critério de linha
        # corrompida acima.
        return None


def _masked_headers(
    raw_headers: object, known_secret_values: tuple[str, ...]
) -> tuple[HttpTransactionHeader, ...]:
    if not isinstance(raw_headers, dict):
        return ()
    return tuple(
        HttpTransactionHeader(
            name=str(name), value=mask_all_occurrences(str(value), known_secret_values)
        )
        for name, value in raw_headers.items()
    )


def _masked_body(raw_body: object, known_secret_values: tuple[str, ...]) -> str | None:
    # request_body/response_body chegam do conftest.py gerado já
    # json-seguros (_json_safe lá), mas podem ser dict/list/escalar em vez
    # de string — normalizados para texto AQUI (nunca no conftest.py, que
    # não sabe o que é secret) para que o mascaramento sempre opere sobre
    # uma string simples, cobrindo o body inteiro independente do quão
    # aninhado um valor secret esteja.
    if raw_body is None:
        return None
    text = raw_body if isinstance(raw_body, str) else json.dumps(raw_body, ensure_ascii=False)
    return mask_all_occurrences(text, known_secret_values)


# --- P1.1 (detalhamento de assertions): resultado de cada assertion --------


def _read_assertion_results(
    results_path: str, known_secret_values: tuple[str, ...]
) -> tuple[AssertionResult, ...]:
    # Mesmo raciocínio de _read_http_transactions: arquivo ausente (suíte
    # sem nenhuma assertion registrada, ou gerada antes da P1.1) nunca é
    # erro, só "nada pra reportar".
    path = Path(results_path)
    if not path.exists():
        return ()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return ()

    results: list[AssertionResult] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            # Mesmo critério de linha corrompida de _read_http_transactions
            # — nunca invalida o resto, evidência é best-effort.
            continue
        result = _parse_assertion_result_entry(entry, known_secret_values)
        if result is not None:
            results.append(result)
    return tuple(results)


def _parse_assertion_result_entry(
    entry: object, known_secret_values: tuple[str, ...]
) -> AssertionResult | None:
    if not isinstance(entry, dict):
        return None
    try:
        return AssertionResult(
            test_id=str(entry["test_id"]),
            name=mask_all_occurrences(str(entry["name"]), known_secret_values),
            expected=_masked_scalar(entry["expected"], known_secret_values),
            actual=_masked_scalar(entry["actual"], known_secret_values),
            status=str(entry["status"]),
            precision=str(entry["precision"]),
            reason=mask_all_occurrences(str(entry["reason"]), known_secret_values),
        )
    except (KeyError, TypeError, ValueError):
        # Entrada estruturalmente inválida — descartada, nunca inventada,
        # mesmo critério de _parse_transaction_entry.
        return None


def _masked_scalar(value: object, known_secret_values: tuple[str, ...]) -> object:
    # expected/actual normalmente são escalares simples (status HTTP,
    # "presente"/"ausente", "válido"/"inválido: ...") — preserva o tipo
    # original (int/float/bool/None) quando não há nada pra mascarar;
    # string é mascarada; qualquer outra coisa (dict/list, não esperado
    # aqui mas nunca assumido impossível) vira texto mascarado, nunca
    # perdido silenciosamente.
    if isinstance(value, str):
        return mask_all_occurrences(value, known_secret_values)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return mask_all_occurrences(json.dumps(value, ensure_ascii=False), known_secret_values)


# --- P1.3 (Trace em falha): traces .zip (um por teste que falhou) ----------
# --- P1.5 (infrastructure failure das evidências): toda falha REAL na ------
# --- infraestrutura de captura/masking/leitura do Trace vira uma -----------
# --- InfrastructureFailure explícita (nunca silenciosa), correlacionada ---
# --- por test_id quando ele já é conhecido — nunca inventado. -------------

# "source" da InfrastructureFailure (P1.5): identifica QUAL evidência
# falhou — hoje só existe uma, mas o campo é aberto para futuras
# evidências sem forçar todo consumidor a assumir que só existe uma.
_EVIDENCE_SOURCE_PLAYWRIGHT_TRACE = "playwright_trace"


def _evidence_failure(
    test_id: str, message: str, known_secret_values: tuple[str, ...]
) -> InfrastructureFailure:
    # Mensagens são sempre texto fixo/genérico escrito por este módulo
    # (nunca a mensagem bruta de uma exceção do Playwright, que poderia
    # ecoar detalhes de uma chamada HTTP) — mesmo assim, passam pelo MESMO
    # masking de known_secret_values usado no resto da evidência, como
    # defesa em profundidade.
    return InfrastructureFailure(
        failure_type=InfrastructureFailureType.EVIDENCE_PERSISTENCE_FAILED,
        message=mask_all_occurrences(message, known_secret_values),
        source=_EVIDENCE_SOURCE_PLAYWRIGHT_TRACE,
        test_id=test_id,
    )


def _read_trace_artifacts(
    manifest_path: str, known_secret_values: tuple[str, ...]
) -> tuple[tuple[TraceArtifact, ...], tuple[InfrastructureFailure, ...]]:
    # Mesmo raciocínio de _read_http_transactions/_read_assertion_results:
    # manifesto ausente (nenhum teste falhou, ou suíte gerada antes da
    # P1.3) nunca é erro, só "nada pra reportar" — e nunca cria a pasta de
    # saída mascarada à toa.
    path = Path(manifest_path)
    if not path.exists():
        return (), ()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return (), ()

    # Cada linha é ou um trace bem-sucedido ({"test_id":, "path":}) ou uma
    # falha de captura/finalização já sinalizada pelo próprio conftest.py
    # gerado ({"test_id":, "error": <nome da classe da exceção>} — ver
    # _finish_trace). Uma linha sem nenhuma das duas formas reconhecidas,
    # ou sem test_id, é descartada sem gerar InfrastructureFailure — nunca
    # inventa um test_id (regra explícita do bloco).
    success_entries: list[tuple[str, str]] = []
    evidence_failures: list[InfrastructureFailure] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            # Mesmo critério de linha corrompida das demais evidências —
            # nunca invalida o resto, best-effort.
            continue
        if not isinstance(entry, dict):
            continue
        test_id = entry.get("test_id")
        if not isinstance(test_id, str):
            continue
        raw_path = entry.get("path")
        if isinstance(raw_path, str):
            success_entries.append((test_id, raw_path))
            continue
        error_type = entry.get("error")
        if isinstance(error_type, str):
            evidence_failures.append(
                _evidence_failure(
                    test_id,
                    f"Falha ao finalizar o Trace ({error_type}); nenhum artefato foi gerado.",
                    known_secret_values,
                )
            )

    if not success_entries:
        return (), tuple(evidence_failures)

    # Diretório próprio (nunca dentro de report_dir, removido pelo
    # chamador de run() ao final): a posse passa para quem for persistir o
    # ExecutionResult (PersistExecutionResultUseCase move estes arquivos
    # para o diretório final e só então pode remover este). Criado só
    # quando existe pelo menos um trace de verdade a processar — nunca uma
    # pasta temporária vazia por execução.
    masked_dir = Path(tempfile.mkdtemp(prefix="api-quality-agent-pytest-traces-masked-"))

    artifacts: list[TraceArtifact] = []
    for test_id, raw_path in success_entries:
        source = Path(raw_path)
        if not source.is_file():
            # Trace referenciado no manifesto mas nunca finalizado (ex.:
            # processo morto abruptamente por timeout no meio do
            # tracing.stop) — nunca inventa um artefato para um arquivo
            # que não existe; registra a falha de evidência explicitamente.
            evidence_failures.append(
                _evidence_failure(
                    test_id,
                    "Trace referenciado não foi encontrado após a execução; "
                    "nenhum artefato foi persistido.",
                    known_secret_values,
                )
            )
            continue
        destination = masked_dir / f"{uuid.uuid4().hex}.zip"
        try:
            mask_trace_archive(
                source_path=source,
                destination_path=destination,
                known_secret_values=known_secret_values,
            )
        except Exception:  # noqa: S112, BLE001 - best-effort: só descarta este trace específico
            # P1.4 (hardening) — CRÍTICO: se o masking falhar (.zip
            # corrompido/vazio/truncado, disco cheio etc.), NUNCA persistir
            # o bruto como fallback (fail-safe: "não consegui mascarar"
            # nunca vira "persisto o original mesmo assim"). Também nunca
            # deixa um .zip parcial (mesmo que só com conteúdo já
            # mascarado até o ponto da falha) órfão em masked_dir — o
            # zipfile finaliza um arquivo válido mas incompleto no
            # __exit__ do "with" mesmo quando a exceção interrompe o loop
            # de _mask_trace_archive, então o arquivo pode existir aqui.
            # P1.5: a falha agora é registrada explicitamente, nunca só
            # descartada em silêncio.
            destination.unlink(missing_ok=True)
            evidence_failures.append(
                _evidence_failure(
                    test_id,
                    "Falha ao mascarar o Trace; artefato não persistido por segurança.",
                    known_secret_values,
                )
            )
            continue
        artifacts.append(
            TraceArtifact(type="playwright-trace", test_id=test_id, path=str(destination))
        )

    if not artifacts:
        # Nenhum trace deste manifesto sobreviveu ao masking — masked_dir
        # nunca chega a ser referenciado por nenhum TraceArtifact, então
        # nunca seria limpo por quem persiste o ExecutionResult
        # (PersistExecutionResultUseCase só move/limpa o que está
        # referenciado). Remove aqui para nunca deixar um diretório
        # temporário órfão.
        shutil.rmtree(masked_dir, ignore_errors=True)
    return tuple(artifacts), tuple(evidence_failures)
