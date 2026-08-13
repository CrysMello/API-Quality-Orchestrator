import argparse
from collections.abc import Callable
from dataclasses import dataclass

from api_quality_agent.application.orchestration import (
    CollectionGenerationResult,
    PlaywrightGenerationResult,
)
from api_quality_agent.cli import bootstrap, collection_selection
from api_quality_agent.cli.exit_codes import OPERATION_CANCELLED, SUCCESS
from api_quality_agent.cli.interactive import OperationCancelled, confirm
from api_quality_agent.domain.exceptions import InputError
from api_quality_agent.domain.models import PostmanEnvironment

# Destinos aceitos por --target (Parte 04 do plano de ação Playwright).
# "postman" é o default: preserva 100% o comportamento anterior à
# existência desta flag para quem não a usa.
_TARGET_POSTMAN = "postman"
_TARGET_PLAYWRIGHT = "playwright"
_TARGET_ALL = "all"
_TARGET_CHOICES = (_TARGET_POSTMAN, _TARGET_PLAYWRIGHT, _TARGET_ALL)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = subparsers.add_parser(
        "generate", help="Gera e aplica testes na Collection selecionada."
    )
    collection_selection.add_selection_arguments(parser)
    parser.add_argument(
        "-f",
        "--file",
        dest="file",
        default=None,
        metavar="COLLECTION_JSON",
        help=(
            "Gera os testes a partir de uma Collection exportada localmente "
            "(arquivo .json), sem conectar à API do Postman."
        ),
    )
    parser.add_argument(
        "--openapi-file",
        dest="openapi_file",
        default=None,
        metavar="OPENAPI_JSON",
        help=(
            "Gera uma Collection completa (com testes já embutidos) a partir de "
            "uma especificação OpenAPI/Swagger local, sem conectar à API do Postman."
        ),
    )
    parser.add_argument(
        "--contract-file",
        dest="contract_file",
        default=None,
        metavar="CONTRATO_XLSX",
        help=(
            "Usa um contrato de API declarado numa planilha Excel (.xlsx) como "
            "fonte de schema para as requests pareadas, em vez de inferir só "
            "de Examples salvos. Combinável com a seleção normal da Collection "
            "ou com --file; endpoints sem contrato declarado continuam usando "
            "a inferência de sempre."
        ),
    )
    parser.add_argument(
        "--collection-path-prefix",
        dest="collection_path_prefix",
        default=None,
        metavar="PREFIXO",
        help=(
            "Prefixo fixo de path presente nas requests da Collection mas ausente "
            "do path declarado no contrato (ex.: prefixo de gateway). Removido só "
            "por correspondência exata de segmentos no início do path. Requer "
            "--contract-file."
        ),
    )
    parser.add_argument(
        "--strict-contract-match",
        dest="strict_contract_match",
        action="store_true",
        help=(
            "Falha o comando (exit code 1) se algum endpoint ficar sem contrato "
            "correspondente (UNMATCHED) ou com correspondência ambígua (AMBIGUOUS). "
            "O Contract Match Report ainda é gerado e persistido antes da falha. "
            "Requer --contract-file."
        ),
    )
    parser.add_argument(
        "-y",
        "--yes",
        dest="yes",
        action="store_true",
        help="Não solicitar confirmação final.",
    )
    parser.add_argument(
        "--target",
        dest="target",
        default=_TARGET_POSTMAN,
        choices=_TARGET_CHOICES,
        help=(
            'Destino da geração: "postman" (padrão — comportamento atual, '
            'inalterado), "playwright" (estrutura da suíte já persistida; '
            "conteúdo completo das asserções ainda não implementado) ou "
            '"all" (os dois).'
        ),
    )
    parser.add_argument(
        "-e",
        "--environment",
        dest="environment",
        default=None,
        metavar="ENVIRONMENT_JSON",
        help=(
            "Arquivo de Environment do Postman a usar na geração (mesmo "
            "formato aceito por 'run -e'). Disponibilizado ao gerador "
            "Playwright; nenhum valor do Environment é embutido no código "
            "gerado nesta versão."
        ),
    )
    parser.set_defaults(handler=_handle_generate)


@dataclass(frozen=True)
class _GenerationOutcome:
    # postman_result/playwright_result ficam None quando o --target
    # correspondente não foi solicitado — nunca "vazios por engano": a
    # ausência é sempre porque aquele lado não foi chamado.
    postman_result: CollectionGenerationResult | None
    playwright_result: PlaywrightGenerationResult | None


def _generate_for_target(
    target: str,
    generate_postman: Callable[[], CollectionGenerationResult],
    generate_playwright: Callable[[], PlaywrightGenerationResult],
) -> _GenerationOutcome:
    # Ponto único de roteamento entre os dois geradores, reaproveitado pelos
    # três caminhos de entrada (online, --file, --openapi-file). Cada lado
    # só é chamado (e só cria seus próprios artefatos/diretórios) quando o
    # target pedido realmente o inclui — postman nunca chama playwright e
    # vice-versa (Parte 04); "all" chama os dois, cada um isolado no seu
    # próprio execution_id (Parte 06).
    postman_result = generate_postman() if target in (_TARGET_POSTMAN, _TARGET_ALL) else None
    playwright_result = (
        generate_playwright() if target in (_TARGET_PLAYWRIGHT, _TARGET_ALL) else None
    )
    return _GenerationOutcome(postman_result=postman_result, playwright_result=playwright_result)


def _load_environment(
    context: "bootstrap.CliContext | bootstrap.OfflineCliContext",
    environment_path: str | None,
) -> PostmanEnvironment | None:
    # Validado/interpretado uma única vez por invocação, independente do
    # --target escolhido (Parte 09) — dá erro claro sobre o arquivo em si
    # mesmo quando o target atual não chega a consumi-lo. Nunca busca o
    # Environment remotamente: só arquivo local, via o mesmo InputResolver
    # já usado para -f/--openapi-file.
    if environment_path is None:
        return None
    resolved = context.input_resolver.resolve_from_file(environment_path)
    return context.environment_parser.parse(resolved)


def _handle_generate(args: argparse.Namespace) -> int:
    collection_selection.validate_selection_arguments(args, extra_fields=("file", "openapi_file"))
    if args.contract_file is not None and args.openapi_file is not None:
        raise InputError("--contract-file não pode ser combinado com --openapi-file.")
    if args.collection_path_prefix is not None and args.contract_file is None:
        raise InputError("--collection-path-prefix requer --contract-file.")
    if args.strict_contract_match and args.contract_file is None:
        raise InputError("--strict-contract-match requer --contract-file.")

    if args.file is not None:
        return _handle_generate_from_file(args)

    if args.openapi_file is not None:
        return _handle_generate_from_openapi_file(args)

    context = bootstrap.build_context()
    workspace_ref = bootstrap.resolve_active_workspace(context)
    environment = _load_environment(context, args.environment)

    try:
        selected = collection_selection.select_collection(context, workspace_ref.id, args)

        print(f"Workspace: {workspace_ref.name}")
        print(f"Collection selecionada: {selected.name}")
        print(f"Collection ID: {selected.id}\n")

        if not args.yes and not confirm():
            print("Operação cancelada pelo usuário.")
            return OPERATION_CANCELLED
    except OperationCancelled:
        print("Operação cancelada pelo usuário.")
        return OPERATION_CANCELLED

    print(f"\nCollection selecionada:\n{selected.name}\n")
    print("Gerando testes...")

    # collection_id é sempre passado explicitamente: a Collection já foi
    # resolvida acima (por ID, nome, índice ou interativamente) — isso é
    # sempre uma seleção temporária (ResolveCollectionUseCase nunca
    # persiste), nunca altera a seleção ativa salva em disco.
    def _generate_postman() -> CollectionGenerationResult:
        if args.contract_file is not None:
            return context.generate_with_contract_use_case.execute_online(
                contract_file=args.contract_file,
                collection_id=selected.id,
                collection_path_prefix=args.collection_path_prefix,
                strict_contract_match=args.strict_contract_match,
            )
        return context.generate_use_case.execute(collection_id=selected.id)

    def _generate_playwright() -> PlaywrightGenerationResult:
        # Busca o documento separadamente (só quando de fato solicitado):
        # é uma leitura, não aciona nenhuma geração/merge Postman.
        document = context.collection_repository.get(selected.id)
        return context.generate_playwright_use_case.execute(
            document=document,
            workspace_id=workspace_ref.id,
            workspace_name=workspace_ref.name,
            collection_id=selected.id,
            collection_name=selected.name,
            environment=environment,
        )

    outcome = _generate_for_target(args.target, _generate_postman, _generate_playwright)

    _print_generation_summary(outcome)
    return SUCCESS


def _handle_generate_from_file(args: argparse.Namespace) -> int:
    context = bootstrap.build_offline_context()

    resolved_input = context.input_resolver.resolve_from_file(args.file)
    document = context.collection_parser.parse(resolved_input)
    environment = _load_environment(context, args.environment)

    print(f"Arquivo: {args.file}")
    print(f"Collection: {document.name}\n")

    try:
        if not args.yes and not confirm():
            print("Operação cancelada pelo usuário.")
            return OPERATION_CANCELLED
    except OperationCancelled:
        print("Operação cancelada pelo usuário.")
        return OPERATION_CANCELLED

    print("Gerando testes (modo local, sem conexão com a API do Postman)...")

    def _generate_postman() -> CollectionGenerationResult:
        if args.contract_file is not None:
            return context.generate_with_contract_use_case.execute_offline(
                contract_file=args.contract_file,
                document=document,
                collection_path_prefix=args.collection_path_prefix,
                strict_contract_match=args.strict_contract_match,
            )
        return context.generate_from_file_use_case.execute(document=document)

    def _generate_playwright() -> PlaywrightGenerationResult:
        return context.generate_playwright_use_case.execute(
            document=document, environment=environment
        )

    outcome = _generate_for_target(args.target, _generate_postman, _generate_playwright)

    _print_generation_summary(outcome)
    return SUCCESS


def _handle_generate_from_openapi_file(args: argparse.Namespace) -> int:
    context = bootstrap.build_offline_context()

    resolved_input = context.input_resolver.resolve_from_file(args.openapi_file)
    specification = context.openapi_parser.parse(resolved_input)
    environment = _load_environment(context, args.environment)

    print(f"Arquivo: {args.openapi_file}")
    print(f"Especificação: {specification.title or specification.spec_type.value}\n")

    try:
        if not args.yes and not confirm():
            print("Operação cancelada pelo usuário.")
            return OPERATION_CANCELLED
    except OperationCancelled:
        print("Operação cancelada pelo usuário.")
        return OPERATION_CANCELLED

    print("Gerando Collection e testes a partir da especificação OpenAPI (modo local)...")

    def _generate_postman() -> CollectionGenerationResult:
        return context.generate_from_openapi_use_case.execute(specification=specification)

    def _generate_playwright() -> PlaywrightGenerationResult:
        # Mesma conversão pura usada por generate_from_openapi_use_case,
        # sem acionar a geração/persistência do lado Postman.
        playwright_document = context.openapi_collection_converter.convert(specification)
        return context.generate_playwright_use_case.execute(
            document=playwright_document, environment=environment
        )

    outcome = _generate_for_target(args.target, _generate_postman, _generate_playwright)

    _print_generation_summary(outcome)
    return SUCCESS


def _print_generation_summary(outcome: _GenerationOutcome) -> None:
    print("Processo concluído com sucesso.\n")
    if outcome.postman_result is not None:
        _print_postman_summary(outcome.postman_result)
    if outcome.playwright_result is not None:
        if outcome.postman_result is not None:
            print()
        _print_playwright_summary(outcome.playwright_result)


def _print_postman_summary(result: CollectionGenerationResult) -> None:
    print("Postman:")
    print(f"  Endpoints processados: {len(result.endpoint_outcomes)}")
    failed_outcomes = [outcome for outcome in result.endpoint_outcomes if outcome.error is not None]
    if failed_outcomes:
        print(f"    Com falha: {len(failed_outcomes)}")
    print(f"  Diff possui mudanças: {result.diff.has_changes}")
    print(f"  Artefatos salvos: {len(result.artifact_locations)}")
    for location in result.artifact_locations:
        print(f"    - {location.path}")


def _print_playwright_summary(result: PlaywrightGenerationResult) -> None:
    print("Playwright:")
    print(f"  Arquivos gerados: {len(result.generated_file_paths)}")
    for relative_path in result.generated_file_paths:
        print(f"    - {relative_path}")
    if result.warning_count:
        print(f"  Warnings: {result.warning_count}")
