import argparse
import sys
from datetime import datetime
from pathlib import Path

from api_quality_agent.cli import bootstrap, collection_selection
from api_quality_agent.cli.exit_codes import (
    FUNCTIONAL_FAILURE,
    INTEGRATION_FAILURE,
    OPERATION_CANCELLED,
    SUCCESS,
)
from api_quality_agent.cli.interactive import OperationCancelled
from api_quality_agent.domain.exceptions import (
    InputError,
    InputFileNotFoundError,
    InvalidPostmanEnvironmentError,
)
from api_quality_agent.domain.models import ExecutionResult, InfrastructureFailureType
from api_quality_agent.domain.policies import ensure_non_empty_id
from api_quality_agent.parsers import PostmanEnvironmentParser

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# Destinos aceitos por --target (Gap 2 da revisão P0 do Playwright). "postman"
# é o default: preserva 100% o comportamento anterior a esta flag. Só "postman"
# e "playwright" — nunca "all" aqui (diferente de generate --target): rodar
# os dois motores numa única chamada produziria dois ExecutionResult/result.json
# distintos, e não há um pedido explícito pra decidir como isso seria
# apresentado numa única execução do comando.
_TARGET_POSTMAN = "postman"
_TARGET_PLAYWRIGHT = "playwright"
_TARGET_CHOICES = (_TARGET_POSTMAN, _TARGET_PLAYWRIGHT)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = subparsers.add_parser(
        "run", help="Executa a Collection selecionada via Newman, ou a suíte Playwright já gerada."
    )
    collection_selection.add_selection_arguments(parser)
    parser.add_argument(
        "-f",
        "--file",
        dest="file",
        default=None,
        metavar="PATH",
        help=(
            "Para --target postman (padrão): Collection exportada localmente "
            "(arquivo .json), sem conectar à API do Postman. Para --target "
            "playwright: diretório da suíte já gerada por "
            "'generate --target playwright' (obrigatório nesse caso)."
        ),
    )
    parser.add_argument(
        "--target",
        dest="target",
        default=_TARGET_POSTMAN,
        choices=_TARGET_CHOICES,
        help=(
            'Motor de execução: "postman" (padrão — comportamento atual, '
            'inalterado, via Newman) ou "playwright" (roda a suíte já '
            "gerada via pytest)."
        ),
    )
    parser.add_argument(
        "--newman-executable",
        dest="newman_executable",
        default=None,
        metavar="CAMINHO",
        help=(
            "Caminho do executável do Newman (só para --target postman). "
            "Precedência: esta flag > variável de ambiente NEWMAN_EXECUTABLE "
            "> \"newman\"."
        ),
    )
    parser.add_argument(
        "--pytest-executable",
        dest="pytest_executable",
        default=None,
        metavar="CAMINHO",
        help=(
            "Caminho do executável do pytest (só para --target playwright). "
            "Precedência: esta flag > variável de ambiente PYTEST_EXECUTABLE "
            "> \"pytest\"."
        ),
    )
    parser.add_argument(
        "-e",
        "--environment",
        dest="environment",
        default=None,
        metavar="ENVIRONMENT_JSON",
        help=(
            "Arquivo de Environment do Postman. Para --target postman, "
            "usado na execução em si. Para --target playwright, usado só "
            "para mascarar valores marcados \"type\": \"secret\" nas "
            "evidências (stdout/stderr/mensagens de falha) — a execução em "
            "si lê variáveis diretamente do ambiente do processo (AQO_*)."
        ),
    )
    parser.set_defaults(handler=_handle_run)


def _handle_run(args: argparse.Namespace) -> int:
    collection_selection.validate_selection_arguments(args, extra_fields=("file",))

    if args.target == _TARGET_PLAYWRIGHT:
        return _handle_run_playwright(args)

    if args.file is not None:
        return _handle_run_from_file(args)

    context = bootstrap.build_context(newman_executable=args.newman_executable)
    workspace_ref = bootstrap.resolve_active_workspace(context)

    try:
        selected = collection_selection.select_collection(context, workspace_ref.id, args)

        print(f"Executando '{selected.name}' via Newman...")

        started_at = datetime.now()
        try:
            result = context.run_use_case.execute(
                collection_id=selected.id, environment_path=args.environment
            )
        except KeyboardInterrupt:
            raise OperationCancelled() from None
        finished_at = datetime.now()
    except OperationCancelled:
        print("Operação cancelada pelo usuário.")
        return OPERATION_CANCELLED

    if result.infrastructure_failure is not None:
        _print_infrastructure_failure(result)
        return INTEGRATION_FAILURE

    _print_summary(workspace_ref.name, selected.name, result, started_at, finished_at)
    _persist_result(
        context.persist_execution_result_use_case,
        result,
        collection_id=selected.id,
        collection_name=selected.name,
        workspace_id=workspace_ref.id,
        workspace_name=workspace_ref.name,
        started_at=started_at,
        finished_at=finished_at,
    )

    return _final_exit_code(result)


def _handle_run_from_file(args: argparse.Namespace) -> int:
    context = bootstrap.build_offline_run_context(newman_executable=args.newman_executable)

    resolved_input = context.input_resolver.resolve_from_file(args.file)
    document = context.collection_parser.parse(resolved_input)

    print(f"Executando '{document.name}' via Newman (arquivo local)...")

    started_at = datetime.now()
    try:
        result = context.run_use_case.execute(
            local_collection_path=args.file, environment_path=args.environment
        )
    except KeyboardInterrupt:
        print("Operação cancelada pelo usuário.")
        return OPERATION_CANCELLED
    finished_at = datetime.now()

    if result.infrastructure_failure is not None:
        _print_infrastructure_failure(result)
        return INTEGRATION_FAILURE

    _print_summary(None, document.name, result, started_at, finished_at)
    _persist_result(
        context.persist_execution_result_use_case,
        result,
        collection_id=None,
        collection_name=document.name,
        workspace_id=None,
        workspace_name=None,
        started_at=started_at,
        finished_at=finished_at,
    )

    return _final_exit_code(result)


def _handle_run_playwright(args: argparse.Namespace) -> int:
    # Fluxo pedido: run_command -> target=playwright -> PlaywrightAdapter ->
    # pytest -> ExecutionResult -> persistência. Reaproveita o MESMO
    # ExecutionResult/PersistExecutionResultUseCase do caminho Newman — nunca
    # um segundo pipeline de execução ou de persistência.
    if args.file is None:
        raise InputError(
            "Para --target playwright, informe o caminho da suíte já gerada via "
            "--file/-f (ex.: artifacts/.../scripts/playwright)."
        )

    context = bootstrap.build_offline_playwright_run_context(
        pytest_executable=args.pytest_executable
    )
    known_secret_values = _resolve_known_secret_values(context.input_resolver, args.environment)

    print(f"Executando a suíte Playwright em '{args.file}' via pytest...")

    started_at = datetime.now()
    try:
        result = context.playwright_adapter.run(
            tests_path=args.file, known_secret_values=known_secret_values
        )
    except KeyboardInterrupt:
        print("Operação cancelada pelo usuário.")
        return OPERATION_CANCELLED
    finished_at = datetime.now()

    if result.infrastructure_failure is not None:
        _print_playwright_infrastructure_failure(result)
        return INTEGRATION_FAILURE

    _print_summary(None, Path(args.file).name, result, started_at, finished_at)
    print(f"Skipped: {result.skipped_tests}")
    _persist_result(
        context.persist_execution_result_use_case,
        result,
        collection_id=None,
        # collection_name aqui é só um rótulo de exibição/persistência —
        # deriva do nome do diretório da suíte (não há um "nome de
        # Collection" pra Playwright); nunca usado para lógica de negócio.
        collection_name=Path(args.file).name,
        workspace_id=None,
        workspace_name=None,
        started_at=started_at,
        finished_at=finished_at,
    )

    return _final_exit_code(result)


def _resolve_known_secret_values(
    input_resolver: "bootstrap.InputResolver", environment_path: str | None
) -> tuple[str, ...]:
    # Fonte de "quais valores são secret" reaproveitada da MESMA metadata já
    # usada pela geração Playwright (EnvironmentVariable.is_secret, vindo do
    # "type": "secret" do Environment do Postman, via o mesmo parser de
    # domínio que bootstrap.py já usa para --target playwright em generate)
    # — nunca uma lista nova "provável secret" baseada em convenção de nome
    # (AQO_*), que não distingue secret de variável comum. Falha ao ler/
    # parsear o Environment nunca bloqueia a execução: mascaramento é
    # best-effort de segurança, não um requisito funcional do run em si —
    # mesmo espírito defensivo de NewmanAdapter._extract_secret_values.
    # input_resolver vem do CliContext (nunca instanciado aqui): comandos só
    # falam com adapters através do bootstrap (ver test_cli_architecture.py).
    if environment_path is None:
        return ()
    ensure_non_empty_id(environment_path, "environment_path")

    try:
        resolved_input = input_resolver.resolve_from_file(environment_path)
        environment = PostmanEnvironmentParser().parse(resolved_input)
    except (InputFileNotFoundError, InvalidPostmanEnvironmentError, OSError):
        return ()

    return tuple(
        variable.value
        for variable in environment.variables
        if variable.is_secret and variable.enabled and variable.value
    )


def _final_exit_code(result: ExecutionResult) -> int:
    if result.success:
        print("\nExecution finished successfully.")
        return SUCCESS

    print("\nExecution finished with test failures.")
    return FUNCTIONAL_FAILURE


def _persist_result(
    persist_execution_result_use_case: bootstrap.PersistExecutionResultUseCase,
    result: ExecutionResult,
    *,
    collection_id: str | None,
    collection_name: str | None,
    workspace_id: str | None,
    workspace_name: str | None,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    # A execução dos testes e a persistência do resultado são
    # responsabilidades distintas: uma falha ao gravar o result.json nunca
    # transforma uma execução bem-sucedida (ou com falhas de teste) em erro
    # de infraestrutura — só é comunicada como um aviso à parte.
    try:
        location = persist_execution_result_use_case.execute(
            result,
            collection_id=collection_id,
            collection_name=collection_name,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            started_at=started_at,
            finished_at=finished_at,
        )
    except Exception as exc:
        print(f"\nAviso: não foi possível salvar o resultado da execução: {exc}", file=sys.stderr)
        return

    print(f"\nResult saved to:\n  {location.path}")


def _print_summary(
    workspace_name: str | None,
    collection_name: str,
    result: ExecutionResult,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    passed = result.total_assertions - result.failed_assertions
    print("\nExecution Summary")
    print("-" * 40)
    print(f"Workspace: {workspace_name or 'N/A (execução local)'}")
    print(f"Collection: {collection_name}")
    print(f"Started: {started_at.strftime(_TIMESTAMP_FORMAT)}")
    print(f"Finished: {finished_at.strftime(_TIMESTAMP_FORMAT)}")
    print(f"Duration: {result.duration_seconds:.1f} s")
    print(f"Requests: {result.total_requests}")
    print(f"Assertions: {result.total_assertions}")
    print(f"Passed: {passed}")
    print(f"Failed: {result.failed_assertions}")


def _print_infrastructure_failure(result: ExecutionResult) -> None:
    failure = result.infrastructure_failure
    assert failure is not None  # já verificado pelo chamador
    print("\nNewman execution failed due to an infrastructure error.")
    print(failure.message)
    if failure.failure_type == InfrastructureFailureType.EXECUTABLE_NOT_FOUND:
        print(
            "\nConfigure o executável usando:\n"
            "  --newman-executable <caminho>\n"
            "ou:\n"
            "  NEWMAN_EXECUTABLE=<caminho>"
        )


def _print_playwright_infrastructure_failure(result: ExecutionResult) -> None:
    # Função própria (em vez de reaproveitar _print_infrastructure_failure):
    # aquela imprime literalmente "Newman execution failed...", o que seria
    # uma mensagem errada aqui (quem falhou foi o pytest) — nunca alterada
    # para não mudar o texto já impresso pelo caminho Newman.
    failure = result.infrastructure_failure
    assert failure is not None  # já verificado pelo chamador
    print("\nPlaywright execution failed due to an infrastructure error.")
    print(failure.message)
    if failure.failure_type == InfrastructureFailureType.EXECUTABLE_NOT_FOUND:
        print("\nVerifique se o pytest está instalado e disponível no PATH.")
