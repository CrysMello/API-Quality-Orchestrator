import re
from dataclasses import dataclass

# Mesmo critério estrutural usado por
# PostmanRequestNormalizer._is_pure_variable_reference: só um valor que É
# inteiramente uma referência de variável ({{nome}}, nada mais na string) é
# seguro de expor — qualquer outra coisa pode ser um segredo literal
# hardcoded na Collection.
_PURE_VARIABLE_REFERENCE = re.compile(r"^\{\{\s*[^{}]+?\s*\}\}$")


@dataclass(frozen=True, repr=False)
class NormalizedAuthParameter:
    # Uma entrada bruta do array de auth do Postman (ex.: {"key": "token",
    # "value": "{{accessToken}}"} para bearer; {"key": "username", ...} /
    # {"key": "password", ...} para basic; {"key": "key"|"value"|"in", ...}
    # para apikey). Exposto para geradores que precisam montar a própria
    # autenticação (ex.: Playwright, Parte 12) — NormalizedAuth sozinho só
    # classifica o tipo, nunca carregava os parâmetros em si.
    key: str | None
    value: str | None

    def __repr__(self) -> str:
        # repr()/str() nunca podem expor um valor literal (possível
        # segredo hardcoded na Collection) — só uma referência pura a
        # variável é exibida como está. dataclass(repr=False) evita que o
        # __repr__ automático (que mostraria o valor cru) sobrescreva este.
        if self.value is not None and not _PURE_VARIABLE_REFERENCE.match(self.value):
            displayed_value: str | None = "***"
        else:
            displayed_value = self.value
        return f"NormalizedAuthParameter(key={self.key!r}, value={displayed_value!r})"
