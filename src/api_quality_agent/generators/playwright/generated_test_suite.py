from dataclasses import dataclass

from api_quality_agent.generators.playwright.generated_file import GeneratedFile
from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)


@dataclass(frozen=True)
class GeneratedTestSuite:
    # Saída de PlaywrightTestSuiteBuilder.build(...) — a suíte já montada
    # (arquivos por endpoint + conftest.py + o que mais for compartilhado),
    # ainda sem ser persistida em disco. `warnings` aqui é o agregado de
    # todos os GeneratedEndpointTest.warnings mais os que só existem no
    # nível da suíte (ex.: FILE_NAME_COLLISION_RESOLVED, que só faz sentido
    # ao comparar nomes entre endpoints diferentes).
    files: tuple[GeneratedFile, ...]
    warnings: tuple[PlaywrightGenerationWarning, ...]
