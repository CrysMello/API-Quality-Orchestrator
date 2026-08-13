from collections.abc import Callable
from datetime import datetime
from typing import Any

from api_quality_agent.application.orchestration import PlaywrightGenerationResult
from api_quality_agent.domain.models import (
    ExecutionContext,
    ExecutionMode,
    GeneratedArtifact,
    PostmanCollectionDocument,
    PostmanEnvironment,
)
from api_quality_agent.domain.services import ApiAnalysisEngine, TestStrategyEngine
from api_quality_agent.generators.playwright import (
    EndpointTestGenerator,
    GeneratedEndpointTest,
    GeneratedTestSuite,
    PlaywrightTestSuiteBuilder,
)
from api_quality_agent.ports.outbound import ArtifactRepository, SchemaProvider
from api_quality_agent.shared import sanitize_filename_component

# Mesma convenção "local" já usada por GenerateTestsFromDocumentUseCase para
# origens sem Workspace/Collection reais do Postman (--file/--openapi-file).
_LOCAL_WORKSPACE_ID = "local"
_MAX_SLUG_LENGTH = 40
_SLUG_HASH_LENGTH = 8

# Toda a suíte Playwright vive sob esta categoria, à parte de "scripts"
# (onde os .js do Postman continuam, inalterados) — separação física entre
# os dois geradores dentro do mesmo workspace_id/collection_id/execution_id.
PLAYWRIGHT_ARTIFACT_CATEGORY = "scripts/playwright"


class GeneratePlaywrightTestSuiteUseCase:
    # Pipeline independente do AgentOrchestrator (Postman): não faz merge em
    # nenhuma Collection, não participa do DiffEngine — analisa o documento,
    # monta uma TestStrategy por endpoint (mesmo TestStrategyEngine
    # reaproveitado do caminho Postman) e delega a um EndpointTestGenerator +
    # PlaywrightTestSuiteBuilder (contratos da Parte 03) a produção da
    # suíte, persistindo o resultado isolado por execution_id próprio.
    def __init__(
        self,
        analysis_engine: ApiAnalysisEngine,
        schema_provider: SchemaProvider,
        test_strategy_engine: TestStrategyEngine,
        endpoint_test_generator: EndpointTestGenerator,
        suite_builder: PlaywrightTestSuiteBuilder,
        artifact_repository: ArtifactRepository,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._analysis_engine = analysis_engine
        self._schema_provider = schema_provider
        self._test_strategy_engine = test_strategy_engine
        self._endpoint_test_generator = endpoint_test_generator
        self._suite_builder = suite_builder
        self._artifact_repository = artifact_repository
        self._id_factory = id_factory
        self._clock = clock

    def execute(
        self,
        *,
        document: PostmanCollectionDocument,
        workspace_id: str | None = None,
        workspace_name: str | None = None,
        collection_id: str | None = None,
        collection_name: str | None = None,
        environment: PostmanEnvironment | None = None,
    ) -> PlaywrightGenerationResult:
        create_kwargs: dict[str, Any] = {}
        if self._id_factory is not None:
            create_kwargs["id_factory"] = self._id_factory
        if self._clock is not None:
            create_kwargs["clock"] = self._clock

        execution_context = ExecutionContext.create(
            mode=ExecutionMode.ONLINE if workspace_id is not None else ExecutionMode.OFFLINE,
            source="playwright-generation",
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            collection_id=collection_id,
            collection_name=collection_name or document.name,
            **create_kwargs,
        )

        endpoint_tests = self._generate_endpoint_tests(document, environment)
        suite = self._suite_builder.build(endpoint_tests, execution_context)
        self._persist(suite, execution_context, document, collection_id)

        return PlaywrightGenerationResult(
            generated_file_paths=tuple(f.relative_path for f in suite.files),
            warning_count=len(suite.warnings),
        )

    def _generate_endpoint_tests(
        self,
        document: PostmanCollectionDocument,
        environment: PostmanEnvironment | None,
    ) -> list[GeneratedEndpointTest]:
        analyzed_requests = self._analysis_engine.analyze_collection_requests(document)

        endpoint_tests: list[GeneratedEndpointTest] = []
        for analyzed in analyzed_requests:
            resolution = self._schema_provider.resolve(analyzed.raw_request)
            strategy = self._test_strategy_engine.build_strategy(
                analyzed.analysis, response_schema=resolution.schema
            )
            endpoint_tests.append(
                self._endpoint_test_generator.generate_endpoint(
                    strategy, analyzed.normalized_request, environment
                )
            )
        return endpoint_tests

    def _persist(
        self,
        suite: GeneratedTestSuite,
        execution_context: ExecutionContext,
        document: PostmanCollectionDocument,
        collection_id: str | None,
    ) -> None:
        resolved_workspace_id = execution_context.workspace_id or _LOCAL_WORKSPACE_ID
        resolved_collection_id = collection_id or sanitize_filename_component(
            document.name,
            max_length=_MAX_SLUG_LENGTH,
            hash_length=_SLUG_HASH_LENGTH,
            fallback="collection",
        )

        for generated_file in suite.files:
            self._artifact_repository.save(
                workspace_id=resolved_workspace_id,
                collection_id=resolved_collection_id,
                execution_id=execution_context.execution_id,
                artifact=GeneratedArtifact(
                    category=PLAYWRIGHT_ARTIFACT_CATEGORY,
                    relative_path=generated_file.relative_path,
                    content=generated_file.content,
                ),
            )
