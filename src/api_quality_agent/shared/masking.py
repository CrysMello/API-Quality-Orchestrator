from collections.abc import Sequence


def mask_secret(
    value: str,
    *,
    visible_prefix: int = 4,
    visible_suffix: int = 4,
    mask_char: str = "*",
) -> str:
    if not isinstance(value, str):
        raise TypeError("value deve ser uma string.")
    length = len(value)
    if length == 0:
        return value
    if length <= visible_prefix + visible_suffix:
        return mask_char * length
    prefix = value[:visible_prefix]
    suffix = value[-visible_suffix:] if visible_suffix else ""
    masked_length = length - visible_prefix - visible_suffix
    return f"{prefix}{mask_char * masked_length}{suffix}"


def mask_all_occurrences(text: str, secret_values: Sequence[str]) -> str:
    # Mesmo laço já usado internamente pelo NewmanAdapter (newman_adapter.py
    # _mask) — extraído aqui como função pura e reutilizável para que
    # PlaywrightAdapter (e qualquer fonte futura de segredo) use a MESMA
    # lógica, sem duplicar o laço nem exigir alterar o NewmanAdapter
    # existente para importar daqui.
    masked = text
    for value in secret_values:
        if value:
            masked = masked.replace(value, mask_secret(value))
    return masked
