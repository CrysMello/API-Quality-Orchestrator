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
from api_quality_agent.domain.models import (
    ArtifactLocation,
    DiffResult,
    ExecutionContext,
    ExecutionMode,
    GeneratedArtifact,
    PostmanCollectionDocument,
)
from api_quality_agent.ports.outbound import ArtifactRepository

_UNSAFE_SLUG_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")

# Endpoints com paths longos/aninhados podem gerar nomes de arquivo que,
# somados ao caminho completo em artifacts/, ultrapassam o limite de 260
# caracteres do Windows (MAX_PATH), causando FileNotFoundError ao salvar.
# 40 caracteres deixa margem para prefixos de diretório longos (ex.: projeto
# sincronizado via OneDrive) + 3 UUIDs de workspace/collection/execution
# (37 caracteres cada) + "scripts\" + hash + extensão ".js" sem estourar 260.
_MAX_SLUG_LENGTH = 40
_SLUG_HASH_LENGTH = 8

# Sem Workspace/Collection reais (o documento vem de um arquivo local, não da
# API do Postman), os artefatos ainda precisam de um workspace_id/collection_id
# para o isolamento por diretório do ArtifactRepository — "local" identifica
# esse modo de execução, e o nome da Collection do próprio arquivo diferencia
# execuções de arquivos distintos.
_LOCAL_FILE_WORKSPACE_ID = "local"


class GenerateTestsFromDocumentUseCase:
    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        artifact_repository: ArtifactRepository,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._artifact_repository = artifact_repository
        self._id_factory = id_factory
        self._clock = clock

    def execute(self, *, document: PostmanCollectionDocument) -> CollectionGenerationResult:
        create_kwargs: dict[str, Any] = {}
        if self._id_factory is not None:
            create_kwargs["id_factory"] = self._id_factory
        if self._clock is not None:
            create_kwargs["clock"] = self._clock

        execution_context = ExecutionContext.create(
            mode=ExecutionMode.OFFLINE,
            source="local-file",
            collection_name=document.name,
            **create_kwargs,
        )

        result = self._orchestrator.process(document, execution_context)

        artifact_locations = self._save_artifacts(
            result,
            collection_id=_slugify(document.name),
            execution_id=execution_context.execution_id,
        )
        return replace(result, artifact_locations=artifact_locations)

    def _save_artifacts(
        self,
        result: CollectionGenerationResult,
        *,
        collection_id: str,
        execution_id: str,
    ) -> tuple[ArtifactLocation, ...]:
        locations: list[ArtifactLocation] = []

        for outcome in result.endpoint_outcomes:
            if outcome.generated_script is None:
                continue
            artifact = GeneratedArtifact(
                category="scripts",
                relative_path=f"{_slugify(outcome.endpoint_source)}.js",
                content=outcome.generated_script.script,
            )
            locations.append(
                self._artifact_repository.save(
                    workspace_id=_LOCAL_FILE_WORKSPACE_ID,
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
                workspace_id=_LOCAL_FILE_WORKSPACE_ID,
                collection_id=collection_id,
                execution_id=execution_id,
                artifact=diff_artifact,
            )
        )

        return tuple(locations)


def _slugify(value: str) -> str:
    sanitized = _UNSAFE_SLUG_CHARS.sub("_", value).strip("_") or "collection"
    if len(sanitized) <= _MAX_SLUG_LENGTH:
        return sanitized

    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:_SLUG_HASH_LENGTH]
    truncated_length = _MAX_SLUG_LENGTH - _SLUG_HASH_LENGTH - 1
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
