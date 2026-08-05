from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedFile:
    # Um arquivo a ser persistido pela suíte Playwright (endpoint,
    # conftest.py, manifesto...) — puramente um par (caminho relativo,
    # conteúdo). Não depende de filesystem: quem grava é a borda
    # (ArtifactRepository), não este modelo.
    relative_path: str
    content: str
