import os
from dataclasses import dataclass

from api_quality_agent.adapters.config import FileSelectionRepository
from api_quality_agent.adapters.filesystem import (
    HtmlReportWriter,
    InputResolver,
    JsonExecutionResultReader,
    JsonExecutionResultRepository,
    LocalArtifactRepository,
    LocalBackupRepository,
)
from api_quality_agent.adapters.newman import DEFAULT_NEWMAN_EXECUTABLE, NewmanAdapter
from api_quality_agent.adapters.postman import (
    PostmanApiClient,
    PostmanCollectionRepository,
    PostmanWorkspaceRepository,
)
from api_quality_agent.application.orchestration import AgentOrchestrator
from api_quality_agent.application.use_cases import (
    GenerateCollectionFromOpenApiUseCase,
    GenerateCollectionTestsUseCase,
    GeneratePlaywrightTestSuiteUseCase,
    GenerateTestsFromDocumentUseCase,
    GenerateTestsWithContractUseCase,
    GetCurrentWorkspaceUseCase,
    ListCollectionsUseCase,
    ListWorkspacesUseCase,
    LoadExecutionResultUseCase,
    PersistExecutionResultUseCase,
    ResolveCollectionUseCase,
    RunCollectionUseCase,
    SelectWorkspaceUseCase,
    UpdateCollectionUseCase,
    WriteReportUseCase,
)
from api_quality_agent.domain.exceptions import ConfigurationError, InputError, ResourceNotFoundError
from api_quality_agent.domain.models import CollectionRef, WorkspaceRef
from api_quality_agent.domain.services import (
    ApiAnalysisEngine,
    CollectionSelectionService,
    DiffEngine,
    InferenceSchemaProvider,
    ManagedBlockMerger,
    SchemaInferenceEngine,
    TestStrategyEngine,
)
from api_quality_agent.generators import PostmanTestGenerator
from api_quality_agent.generators.playwright import (
    DefaultPlaywrightTestSuiteBuilder,
    PlaceholderEndpointTestGenerator,
)
from api_quality_agent.parsers import (
    ExcelContractParser,
    OpenApiCollectionConverter,
    OpenApiParser,
    PostmanCollectionParser,
    PostmanCollectionSerializer,
)
from api_quality_agent.ports.outbound import (
    ArtifactRepository,
    BackupRepository,
    CollectionRepository,
    CollectionRunner,
    ExecutionResultReader,
    ExecutionResultRepository,
    ReportWriter,
    SelectionRepository,
    WorkspaceRepository,
)
from api_quality_agent.reporting import ReportEngine

POSTMAN_API_KEY_ENV_VAR = "POSTMAN_API_KEY"
NEWMAN_EXECUTABLE_ENV_VAR = "NEWMAN_EXECUTABLE"


@dataclass
class CliContext:
    # Composição de dependências da CLI (equivalente à build_app() usada nos
    # testes de aceitação) — a CLI só monta e invoca os use cases já
    # existentes; nenhuma regra de negócio nova vive aqui.
    workspace_repository: WorkspaceRepository
    collection_repository: CollectionRepository
    selection_repository: SelectionRepository
    selection_service: CollectionSelectionService
    get_current_workspace_use_case: GetCurrentWorkspaceUseCase
    resolve_collection_use_case: ResolveCollectionUseCase
    list_collections_use_case: ListCollectionsUseCase
    generate_use_case: GenerateCollectionTestsUseCase
    list_workspaces_use_case: ListWorkspacesUseCase
    select_workspace_use_case: SelectWorkspaceUseCase
    update_use_case: UpdateCollectionUseCase
    run_use_case: RunCollectionUseCase
    persist_execution_result_use_case: PersistExecutionResultUseCase
    excel_contract_parser: ExcelContractParser
    generate_with_contract_use_case: GenerateTestsWithContractUseCase
    generate_playwright_use_case: GeneratePlaywrightTestSuiteUseCase


def _build_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(
        ApiAnalysisEngine(),
        SchemaInferenceEngine(),
        TestStrategyEngine(),
        PostmanTestGenerator(),
        ManagedBlockMerger(),
        DiffEngine(),
    )


def _build_playwright_use_case(
    artifact_repository: ArtifactRepository,
) -> GeneratePlaywrightTestSuiteUseCase:
    # Composição paralela a _build_orchestrator(), para o caminho Playwright
    # (Parte 06) — pipeline independente, nunca participa do merge/diff
    # Postman. PlaceholderEndpointTestGenerator/DefaultPlaywrightTestSuite
    # Builder são as implementações atuais dos contratos da Parte 03;
    # conteúdo completo das asserções fica para uma etapa futura.
    return GeneratePlaywrightTestSuiteUseCase(
        ApiAnalysisEngine(),
        InferenceSchemaProvider(SchemaInferenceEngine()),
        TestStrategyEngine(),
        PlaceholderEndpointTestGenerator(),
        DefaultPlaywrightTestSuiteBuilder(),
        artifact_repository,
    )


def build_context(
    *,
    artifact_repository: ArtifactRepository | None = None,
    selection_repository: SelectionRepository | None = None,
    backup_repository: BackupRepository | None = None,
    collection_runner: CollectionRunner | None = None,
    newman_executable: str | None = None,
    execution_result_repository: ExecutionResultRepository | None = None,
) -> CliContext:
    api_key = os.environ.get(POSTMAN_API_KEY_ENV_VAR)
    if not api_key:
        raise ConfigurationError(
            f"A variável de ambiente {POSTMAN_API_KEY_ENV_VAR} não está configurada. "
            "Defina-a antes de usar comandos que acessam a API do Postman."
        )

    client = PostmanApiClient(api_key)
    workspace_repository: WorkspaceRepository = PostmanWorkspaceRepository(client)
    collection_repository: CollectionRepository = PostmanCollectionRepository(client)
    effective_selection_repository: SelectionRepository = (
        selection_repository or FileSelectionRepository()
    )
    selection_service = CollectionSelectionService(collection_repository)
    resolve_collection_use_case = ResolveCollectionUseCase(
        selection_service, collection_repository, effective_selection_repository
    )
    get_current_workspace_use_case = GetCurrentWorkspaceUseCase(effective_selection_repository)

    orchestrator = _build_orchestrator()
    effective_artifact_repository = artifact_repository or LocalArtifactRepository()
    generate_use_case = GenerateCollectionTestsUseCase(
        get_current_workspace_use_case,
        resolve_collection_use_case,
        collection_repository,
        orchestrator,
        effective_artifact_repository,
    )
    generate_with_contract_use_case = GenerateTestsWithContractUseCase(
        ExcelContractParser(),
        ApiAnalysisEngine(),
        SchemaInferenceEngine(),
        TestStrategyEngine(),
        PostmanTestGenerator(),
        ManagedBlockMerger(),
        DiffEngine(),
        effective_artifact_repository,
        get_current_workspace_use_case=get_current_workspace_use_case,
        resolve_collection_use_case=resolve_collection_use_case,
        collection_repository=collection_repository,
    )

    return CliContext(
        workspace_repository=workspace_repository,
        collection_repository=collection_repository,
        selection_repository=effective_selection_repository,
        selection_service=selection_service,
        get_current_workspace_use_case=get_current_workspace_use_case,
        resolve_collection_use_case=resolve_collection_use_case,
        list_collections_use_case=ListCollectionsUseCase(
            collection_repository, effective_selection_repository
        ),
        generate_use_case=generate_use_case,
        list_workspaces_use_case=ListWorkspacesUseCase(workspace_repository),
        select_workspace_use_case=SelectWorkspaceUseCase(
            workspace_repository, effective_selection_repository
        ),
        update_use_case=UpdateCollectionUseCase(
            collection_repository, backup_repository or LocalBackupRepository()
        ),
        run_use_case=RunCollectionUseCase(
            get_current_workspace_use_case,
            resolve_collection_use_case,
            collection_repository,
            collection_runner or NewmanAdapter(newman_executable=_resolve_newman_executable(newman_executable)),
        ),
        persist_execution_result_use_case=PersistExecutionResultUseCase(
            execution_result_repository or JsonExecutionResultRepository()
        ),
        excel_contract_parser=ExcelContractParser(),
        generate_with_contract_use_case=generate_with_contract_use_case,
        generate_playwright_use_case=_build_playwright_use_case(effective_artifact_repository),
    )


def _resolve_newman_executable(cli_value: str | None) -> str:
    # Precedência: flag --newman-executable > variável de ambiente
    # NEWMAN_EXECUTABLE > "newman" (padrão do NewmanAdapter). Nenhuma
    # tentativa de localizar o executável automaticamente (ex.: resolver
    # .ps1 para .cmd no Windows) — só essas três fontes explícitas.
    return cli_value or os.environ.get(NEWMAN_EXECUTABLE_ENV_VAR) or DEFAULT_NEWMAN_EXECUTABLE


@dataclass
class OfflineCliContext:
    # Composição paralela a CliContext para o modo "arquivo local": nunca
    # requer POSTMAN_API_KEY nem toca a API do Postman — só parsing local e
    # a mesma pipeline de geração (orquestrador) usada no modo online.
    # openapi_* são usados só pelo caminho `generate --openapi-file`: convertem
    # a especificação numa Collection sintética e reaproveitam a mesma
    # pipeline (generate_from_file_use_case) sem alterá-la.
    input_resolver: InputResolver
    collection_parser: PostmanCollectionParser
    generate_from_file_use_case: GenerateTestsFromDocumentUseCase
    openapi_parser: OpenApiParser
    generate_from_openapi_use_case: GenerateCollectionFromOpenApiUseCase
    excel_contract_parser: ExcelContractParser
    generate_with_contract_use_case: GenerateTestsWithContractUseCase
    generate_playwright_use_case: GeneratePlaywrightTestSuiteUseCase
    # Exposto separadamente (não só encapsulado dentro de
    # generate_from_openapi_use_case) para permitir converter a
    # especificação numa Collection sintética sem acionar a geração Postman
    # — necessário quando --target playwright é usado com --openapi-file
    # (ver generate_command.py): a conversão é pura, sem efeito colateral.
    openapi_collection_converter: OpenApiCollectionConverter


def build_offline_context(
    *,
    artifact_repository: ArtifactRepository | None = None,
) -> OfflineCliContext:
    effective_artifact_repository = artifact_repository or LocalArtifactRepository()
    generate_from_file_use_case = GenerateTestsFromDocumentUseCase(
        _build_orchestrator(), effective_artifact_repository
    )
    return OfflineCliContext(
        input_resolver=InputResolver(),
        collection_parser=PostmanCollectionParser(),
        generate_from_file_use_case=generate_from_file_use_case,
        openapi_parser=OpenApiParser(),
        generate_from_openapi_use_case=GenerateCollectionFromOpenApiUseCase(
            OpenApiCollectionConverter(),
            generate_from_file_use_case,
            PostmanCollectionSerializer(),
            effective_artifact_repository,
        ),
        excel_contract_parser=ExcelContractParser(),
        generate_with_contract_use_case=GenerateTestsWithContractUseCase(
            ExcelContractParser(),
            ApiAnalysisEngine(),
            SchemaInferenceEngine(),
            TestStrategyEngine(),
            PostmanTestGenerator(),
            ManagedBlockMerger(),
            DiffEngine(),
            effective_artifact_repository,
        ),
        generate_playwright_use_case=_build_playwright_use_case(effective_artifact_repository),
        openapi_collection_converter=OpenApiCollectionConverter(),
    )


@dataclass
class OfflineRunCliContext:
    # Composição paralela a CliContext para `run --file`: nunca requer
    # POSTMAN_API_KEY nem toca a API do Postman — RunCollectionUseCase é
    # montado só com as dependências necessárias para
    # execute(local_collection_path=...) (as de Workspace/Postman ficam None,
    # nunca usadas nesse caminho). input_resolver/collection_parser servem
    # só para validar o arquivo e extrair o nome da Collection para exibição
    # e persistência — a execução em si (Newman) usa o arquivo original.
    input_resolver: InputResolver
    collection_parser: PostmanCollectionParser
    run_use_case: RunCollectionUseCase
    persist_execution_result_use_case: PersistExecutionResultUseCase


def build_offline_run_context(
    *,
    collection_runner: CollectionRunner | None = None,
    newman_executable: str | None = None,
    execution_result_repository: ExecutionResultRepository | None = None,
) -> OfflineRunCliContext:
    return OfflineRunCliContext(
        input_resolver=InputResolver(),
        collection_parser=PostmanCollectionParser(),
        run_use_case=RunCollectionUseCase(
            None,
            None,
            None,
            collection_runner or NewmanAdapter(newman_executable=_resolve_newman_executable(newman_executable)),
        ),
        persist_execution_result_use_case=PersistExecutionResultUseCase(
            execution_result_repository or JsonExecutionResultRepository()
        ),
    )


@dataclass
class ReportCliContext:
    # Composição paralela a CliContext para o comando `report`: nunca requer
    # POSTMAN_API_KEY nem toca a API do Postman — report só lê um result.json
    # já persistido localmente e gera o HTML a partir dele.
    load_execution_result_use_case: LoadExecutionResultUseCase
    write_report_use_case: WriteReportUseCase
    report_engine: ReportEngine


def build_report_context(
    *,
    execution_result_reader: ExecutionResultReader | None = None,
    report_writer: ReportWriter | None = None,
) -> ReportCliContext:
    return ReportCliContext(
        load_execution_result_use_case=LoadExecutionResultUseCase(
            execution_result_reader or JsonExecutionResultReader()
        ),
        write_report_use_case=WriteReportUseCase(report_writer or HtmlReportWriter()),
        report_engine=ReportEngine(),
    )


def resolve_active_workspace(context: CliContext) -> WorkspaceRef:
    workspace_id = context.get_current_workspace_use_case.execute()
    if not workspace_id:
        raise InputError(
            "Nenhum Workspace ativo configurado. Selecione um Workspace antes de "
            "usar este comando."
        )
    workspaces = context.workspace_repository.list()
    match = next((workspace for workspace in workspaces if workspace.id == workspace_id), None)
    if match is None:
        raise ResourceNotFoundError(
            f"O Workspace ativo configurado (ID '{workspace_id}') não foi encontrado "
            "ou não está mais acessível com a API Key atual."
        )
    return match


def sort_collections(collections: tuple[CollectionRef, ...]) -> list[CollectionRef]:
    # Ordenação estável e determinística, documentada: nome (alfabético) com
    # o ID como critério de desempate. Só é válida para a listagem da
    # execução atual — o índice nunca deve ser tratado como identificador
    # persistente entre chamadas.
    return sorted(collections, key=lambda collection: (collection.name, collection.id))


def sort_workspaces(workspaces: tuple[WorkspaceRef, ...]) -> list[WorkspaceRef]:
    # Mesma convenção de sort_collections, aplicada a Workspaces.
    return sorted(workspaces, key=lambda workspace: (workspace.name, workspace.id))
