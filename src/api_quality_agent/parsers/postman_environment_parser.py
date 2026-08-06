import json
from typing import Any

from api_quality_agent.domain.exceptions import InvalidPostmanEnvironmentError
from api_quality_agent.domain.models import EnvironmentVariable, PostmanEnvironment, ResolvedInput


class PostmanEnvironmentParser:
    # Mesmo formato já lido por NewmanAdapter._extract_secret_values
    # (adapters/newman/newman_adapter.py) — Environment do Postman:
    # {"name": ..., "values": [{"key", "value", "type", "enabled"}, ...]}.
    # Esta é a versão reaproveitável/testável como parser de domínio, para
    # a geração de testes (Parte 09) usar sem depender do adapter Newman.
    def parse(self, resolved_input: ResolvedInput) -> PostmanEnvironment:
        return self.parse_text(resolved_input.content, source_name=resolved_input.name)

    def parse_text(self, text: str, *, source_name: str = "<content>") -> PostmanEnvironment:
        document = _load_json_document(text, source_name=source_name)
        _validate_basic_structure(document, source_name=source_name)

        variables = tuple(
            variable
            for raw_value in document["values"]
            if (variable := _parse_variable(raw_value)) is not None
        )

        name = document.get("name")
        return PostmanEnvironment(
            name=name if isinstance(name, str) else None,
            variables=variables,
        )


def _load_json_document(text: str, *, source_name: str) -> dict[str, Any]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidPostmanEnvironmentError(
            f"Environment não é um JSON válido em {source_name} "
            f"(linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(document, dict):
        raise InvalidPostmanEnvironmentError(
            f"Environment deve ser um objeto no nível raiz: {source_name}"
        )
    return document


def _validate_basic_structure(document: dict[str, Any], *, source_name: str) -> None:
    if "values" not in document or not isinstance(document["values"], list):
        raise InvalidPostmanEnvironmentError(
            f"Environment não contém 'values' válido: {source_name}"
        )


def _parse_variable(raw_value: Any) -> EnvironmentVariable | None:
    # Entradas malformadas individuais são ignoradas silenciosamente (não
    # invalidam o Environment inteiro) — mesmo espírito defensivo já usado
    # em NewmanAdapter._extract_secret_values.
    if not isinstance(raw_value, dict):
        return None
    key = raw_value.get("key")
    if not isinstance(key, str) or not key:
        return None

    value = raw_value.get("value")
    enabled = raw_value.get("enabled", True)

    return EnvironmentVariable(
        key=key,
        value=value if isinstance(value, str) else "",
        is_secret=raw_value.get("type") == "secret",
        enabled=enabled if isinstance(enabled, bool) else True,
    )
