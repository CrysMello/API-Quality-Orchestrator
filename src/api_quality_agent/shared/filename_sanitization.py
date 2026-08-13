import hashlib
import re

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def sanitize_filename_component(
    value: str,
    *,
    max_length: int,
    hash_length: int,
    fallback: str,
) -> str:
    # Compartilhado entre a persistência de artefatos Postman
    # (GenerateCollectionTestsUseCase/GenerateTestsFromDocumentUseCase) e a
    # nomeação determinística de arquivos Playwright
    # (generators/playwright/endpoint_file_naming.py) — mesmo mecanismo,
    # antes duplicado nos dois use cases Postman. Endpoints com paths
    # longos/aninhados podem gerar nomes de arquivo que, somados ao caminho
    # completo em artifacts/, ultrapassam o limite de 260 caracteres do
    # Windows (MAX_PATH); truncar com um hash determinístico evita colisão
    # silenciosa entre nomes truncados de forma idêntica.
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", value).strip("_") or fallback
    if len(sanitized) <= max_length:
        return sanitized

    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:hash_length]
    truncated_length = max_length - hash_length - 1
    return f"{sanitized[:truncated_length]}_{digest}"
