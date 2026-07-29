import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from api_quality_agent.application.orchestration import (
    AgentOrchestrator,
    CollectionGenerationResult,
)
from api_quality_agent.application.use_cases.get_current_workspace import (
    GetCurrentWorkspaceUseCase,
)
from api_quality_agent.application.use_cases.resolve_collection import ResolveCollectionUseCase
from api_quality_agent.domain.exceptions import InputError
from api_quality_agent.domain.models import (
    ArtifactLocation,
    DiffResult,
    ExecutionContext,
    ExecutionMode,
    GeneratedArtifact,
)
from api_quality_agent.ports.outbound import ArtifactRepository, CollectionRepository

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")

# Endpoints com paths longos/aninhados podem gerar nomes de arquivo que,
# somados ao caminho completo em artifacts/, ultrapassam o limite de 260
# caracteres do Windows (MAX_PATH), causando FileNotFoundError ao salvar.
# 40 caracteres deixa margem para prefixos de diretório longos (ex.: projeto
# sincronizado via OneDrive) + 3 UUIDs de workspace/collection/execution
# (37 caracteres cada) + "scripts\" + hash + extensão ".js" sem estourar 260.
_MAX_FILENAME_LENGTH = 40
_FILENAME_HASH_LENGTH = 8


class GenerateCollectionTestsUseCase:
    def __init__(
        self,
        get_current_workspace_use_case: GetCurrentWorkspaceUseCase,
        resolve_collection_use_case: ResolveCollectionUseCase,
        collection_repository: CollectionRepository,
        orchestrator: AgentOrchestrator,
        artifact_repository: ArtifactRepository,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._get_current_workspace_use_case = get_current_workspace_use_case
        self._resolve_collection_use_case = resolve_collection_use_case
        self._collection_repository = collection_repository
        self._orchestrator = orchestrator
        self._artifact_repository = artifact_repository
        self._id_factory = id_factory
        self._clock = clock

    def execute(
        self,
        *,
        collection_id: str | None = None,
        collection_name: str | None = None,
    ) -> CollectionGenerationResult:
        workspace_id = self._get_current_workspace_use_case.execute()
        if not workspace_id:
            raise InputError(
                "Nenhum Workspace ativo. Selecione um Workspace antes de gerar testes."
            )

        # collection_id/collection_name aqui representam uma seleção temporária:
        # ResolveCollectionUseCase nunca persiste a partir desses overrides.
        collection_ref = self._resolve_collection_use_case.execute(
            collection_id=collection_id, collection_name=collection_name
        )

        # Somente a Collection selecionada é obtida — nunca todas do Workspace.
        document = self._collection_repository.get(collection_ref.id)

        execution_context = self._create_execution_context(
            workspace_id=workspace_id,
            collection_id=collection_ref.id,
            collection_name=collection_ref.name,
        )

        result = self._orchestrator.process(document, execution_context)

        artifact_locations = self._save_artifacts(
            result,
            workspace_id=workspace_id,
            collection_id=collection_ref.id,
            execution_id=execution_context.execution_id,
        )

        return replace(result, artifact_locations=artifact_locations)

    def _create_execution_context(
        self, *, workspace_id: str, collection_id: str, collection_name: str
    ) -> ExecutionContext:
        create_kwargs: dict[str, Any] = {}
        if self._id_factory is not None:
            create_kwargs["id_factory"] = self._id_factory
        if self._clock is not None:
            create_kwargs["clock"] = self._clock

        return ExecutionContext.create(
            mode=ExecutionMode.ONLINE,
            source="postman",
            workspace_id=workspace_id,
            collection_id=collection_id,
            collection_name=collection_name,
            **create_kwargs,
        )

    def _save_artifacts(
        self,
        result: CollectionGenerationResult,
        *,
        workspace_id: str,
        collection_id: str,
        execution_id: str,
    ) -> tuple[ArtifactLocation, ...]:
        locations: list[ArtifactLocation] = []

        for outcome in result.endpoint_outcomes:
            if outcome.generated_script is None:
                continue
            artifact = GeneratedArtifact(
                category="scripts",
                relative_path=f"{_sanitize_filename(outcome.endpoint_source)}.js",
                content=outcome.generated_script.script,
            )
            locations.append(
                self._artifact_repository.save(
                    workspace_id=workspace_id,
                    collection_id=collection_id,
                    execution_id=execution_id,
                    artifact=artifact,
                )
            )

        diff_artifact = GeneratedArtifact(
            category="diffs",
            relative_path="diff.json",
            content=json.dumps(_serialize_diff(result.diff), indent=2, ensure_ascii=False),
        )
        locations.append(
            self._artifact_repository.save(
                workspace_id=workspace_id,
                collection_id=collection_id,
                execution_id=execution_id,
                artifact=diff_artifact,
            )
        )

        return tuple(locations)


def _sanitize_filename(value: str) -> str:
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", value).strip("_") or "endpoint"
    if len(sanitized) <= _MAX_FILENAME_LENGTH:
        return sanitized

    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:_FILENAME_HASH_LENGTH]
    truncated_length = _MAX_FILENAME_LENGTH - _FILENAME_HASH_LENGTH - 1
    return f"{sanitized[:truncated_length]}_{digest}"


def _serialize_diff(diff: DiffResult) -> dict[str, Any]:
    return {
        "entries": [
            {
                "change_type": entry.change_type.value,
                "category": entry.category.value,
                "target": entry.target,
                "risk": entry.risk.value,
                "description": entry.description,
            }
            for entry in diff.entries
        ],
        "has_changes": diff.has_changes,
        "has_removals": diff.has_removals,
        "has_high_risk_changes": diff.has_high_risk_changes,
    }
