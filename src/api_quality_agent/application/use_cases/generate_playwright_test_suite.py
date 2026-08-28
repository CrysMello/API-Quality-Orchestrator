from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from api_quality_agent.application.orchestration import PlaywrightGenerationResult
from api_quality_agent.domain.models import (
    AnalyzedCollectionRequest,
    ExecutionContext,
    ExecutionMode,
    GeneratedArtifact,
    PostmanCollectionDocument,
    PostmanEnvironment,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine, TestStrategyEngine
from api_quality_agent.generators.playwright import (
    EndpointTestGenerator,
    GeneratedEndpointTest,
    GeneratedTestSuite,
    PlaywrightTestSuiteBuilder,
    merge_collection_variables,
)
from api_quality_agent.generators.playwright.endpoint_dependency_linking import (
    EndpointDependencyInput,
    link_endpoint_dependencies,
)
from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
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

        # Variáveis de nível de Collection (ex.: "baseUrl" declarado uma
        # única vez no topo do arquivo) materializadas como Environment de
        # prioridade mais baixa — sem isso, um host/segmento que só
        # existisse ali nunca resolvia sem um Environment explícito. Nunca
        # sobrescreve uma variável de Environment já definida (ver
        # merge_collection_variables).
        effective_environment = merge_collection_variables(environment, document.variables)

        # Fase 1 (dependências entre endpoints): uma TestStrategy por
        # endpoint, na ordem original da Collection — TODAS precisam existir
        # antes da linkagem (Etapa 3): só depois de conhecer as
        # variable_extractions de TODO endpoint é possível saber quem produz
        # o que um outro consome. O PlaywrightEndpointTestGenerator em si
        # nunca vê essa lista completa — recebe só a TestStrategy (já
        # linkada) do seu próprio endpoint, uma de cada vez, como sempre.
        built: list[tuple[AnalyzedCollectionRequest, TestStrategy]] = []
        for analyzed in analyzed_requests:
            resolution = self._schema_provider.resolve(analyzed.raw_request)
            strategy = self._test_strategy_engine.build_strategy(
                analyzed.analysis, response_schema=resolution.schema
            )
            built.append((analyzed, strategy))

        # Fase 2: linkagem (endpoint_dependency_linking.py) — pura, nunca
        # decide nada de geração em si; devolve, por endpoint, o que o
        # generator precisa (variable_usages), o que cada produtor deve
        # tratar como reivindicado, a ordem final de execução/arquivo
        # (Etapa 7) e os warnings de ciclo (Etapa 8).
        linking_result = link_endpoint_dependencies(
            [
                EndpointDependencyInput(
                    endpoint_source=strategy.endpoint_source,
                    normalized_request=analyzed.normalized_request,
                    variable_extractions=strategy.variable_extractions,
                )
                for analyzed, strategy in built
            ]
        )
        warnings_by_endpoint: dict[str, list[PlaywrightGenerationWarning]] = {}
        for warning in linking_result.warnings:
            # endpoint_dependency_linking.py sempre preenche `endpoint`
            # (endpoint_source real, nunca None) para este código — a
            # assinatura de PlaywrightGenerationWarning.endpoint é
            # `str | None` só porque outros pontos de geração podem deixar
            # em branco, nunca este.
            if warning.endpoint is not None:
                warnings_by_endpoint.setdefault(warning.endpoint, []).append(warning)

        # Fase 3: gera cada endpoint na ORDEM final calculada pela linkagem
        # (Etapa 7) — nunca a ordem crua da Collection quando ela difere.
        # variable_extractions chega à TestStrategy já FILTRADA para
        # "reivindicado" (só o que outro endpoint realmente consome); um
        # endpoint sem nenhuma dependência atravessa esta fase byte a byte
        # como antes (strategy sem variable_usages, extractions vazias
        # continuam vazias).
        endpoint_tests: list[GeneratedEndpointTest] = []
        for index in linking_result.order:
            analyzed, strategy = built[index]
            linked = linking_result.linked_endpoints[index]
            linked_strategy = strategy
            if linked.claimed_extraction_names or linked.variable_usages:
                linked_strategy = replace(
                    strategy,
                    variable_extractions=tuple(
                        extraction
                        for extraction in strategy.variable_extractions
                        if extraction.variable_name in linked.claimed_extraction_names
                    ),
                    variable_usages=linked.variable_usages,
                )
            generated = self._endpoint_test_generator.generate_endpoint(
                linked_strategy, analyzed.normalized_request, effective_environment
            )
            extra_warnings = tuple(warnings_by_endpoint.get(strategy.endpoint_source, ()))
            if linked.variable_usages or extra_warnings:
                generated = replace(
                    generated,
                    variable_usages=linked.variable_usages,
                    warnings=generated.warnings + extra_warnings,
                )
            endpoint_tests.append(generated)
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
