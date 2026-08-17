"""Parte 25 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
"Prevenção de Falsos Positivos" — etapa de ENDURECIMENTO, não uma nova
categoria funcional de teste. Este módulo não reimplementa nenhuma regra
de asserção já existente (Partes 16-24): ele só varre o texto JÁ GERADO em
busca de padrões conhecidos que fariam um cenário passar pelo motivo
errado (regra "response.json() is not None não pode ser a única validação
funcional", "status genérico nunca substitui expectativa conhecida",
"nenhum retry para transformar falha em sucesso").

Chamado por DefaultPlaywrightTestSuiteBuilder.build() — "antes da
persistência" (antes de os arquivos virarem GeneratedFile) — nunca dentro
do próprio PlaywrightEndpointTestGenerator, que não tem noção de
persistência. Uma violação aqui é sempre um BUG NO GERADOR (uma regressão
que reintroduziu um padrão proibido), nunca um problema do contrato/
evidência de entrada — por isso levanta imediatamente, sem virar warning
"suave" no manifesto (um warning poderia ser silenciado; esta guarda não
pode).
"""

import re


class GeneratedScenarioQualityError(AssertionError):
    """Levantado quando o conteúdo de um cenário gerado contém um padrão
    proibido pela Parte 25 — sempre um defeito do gerador, nunca algo que
    quem consome a suíte gerada deveria ver."""


# (padrão, explicação) — cada padrão corresponde a UMA guarda da Parte 25;
# nunca duplica uma regra de asserção já existente, só detecta a AUSÊNCIA
# dela (ex.: nenhuma Parte 16-23 gera "response.ok", então sua presença só
# pode vir de uma regressão futura).
_FORBIDDEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"response\.json\(\)\s*is not None"),
        "response.json() is not None nunca pode ser a única validação funcional de um "
        "cenário — use json.loads(response.text()) e as asserções específicas de body/schema "
        "(Partes 18-22).",
    ),
    (
        re.compile(r"response\.ok\b"),
        "response.ok nunca pode substituir uma asserção de status conhecida — status é sempre "
        "exato (Parte 16) ou explicitamente classificado como BROAD (Parte 23), nunca inferido "
        "de response.ok.",
    ),
    (
        re.compile(r"\d+\s*<=\s*response\.status|response\.status\s*[<>]=?\s*\d+"),
        "Nenhum range/classe de status HTTP pode ser gerado como substituto de um valor exato "
        "(Parte 16, regra: nunca 200 <= status < 300 no lugar de um código conhecido).",
    ),
    (
        re.compile(r"time\.sleep\(|for\s+\w*attempt\w*\s+in\s+range\(|while\s+True\s*:"),
        "Nenhum retry pode ser gerado para tentar transformar uma falha em sucesso — o cenário "
        "deve refletir o resultado de uma única chamada.",
    ),
)


def assert_no_false_positive_smells(content: str) -> None:
    for pattern, explanation in _FORBIDDEN_PATTERNS:
        if pattern.search(content):
            raise GeneratedScenarioQualityError(explanation)
