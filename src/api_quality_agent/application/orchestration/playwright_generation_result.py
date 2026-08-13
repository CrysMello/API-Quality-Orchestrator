from dataclasses import dataclass


@dataclass(frozen=True)
class PlaywrightGenerationResult:
    # Vista mínima de generators.playwright.GeneratedTestSuite, segura para
    # a camada CLI consumir: os comandos CLI nunca importam
    # api_quality_agent.generators diretamente (regra de arquitetura
    # verificada em tests/unit/test_cli_architecture.py) — só tipos de
    # application/orchestration ou domain/models, o mesmo papel que
    # CollectionGenerationResult já cumpre para o lado Postman.
    generated_file_paths: tuple[str, ...]
    warning_count: int
