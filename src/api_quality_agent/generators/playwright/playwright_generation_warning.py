from dataclasses import dataclass

from api_quality_agent.generators.playwright.warning_catalog import PLAYWRIGHT_WARNING_CODES


@dataclass(frozen=True)
class PlaywrightGenerationWarning:
    # Equivalente, para o gerador Playwright, ao `GenerationWarning` já
    # existente em generators/generation_warning.py (usado pelo
    # PostmanTestGenerator) — não é o mesmo tipo porque o formato de
    # rastreabilidade é diferente: aqui um warning é sempre vinculado a um
    # endpoint e a um cenário (nunca só a um test_id/field JS), conforme o
    # formato do manifesto de geração (plano de ação Playwright, seção 7).
    #
    # Parte 24 — Padronização de Warnings: REUTILIZA esta mesma estrutura
    # (nenhuma segunda estrutura equivalente criada) só ampliando o que ela
    # já carrega. `code` nunca é texto livre — sempre um dos nomes
    # registrados em warning_catalog.PLAYWRIGHT_WARNING_CODES (regra 1),
    # verificado em __post_init__.
    code: str
    message: str
    endpoint: str | None
    scenario: str | None
    # Derivado automaticamente de `endpoint` quando não informado
    # explicitamente ("MÉTODO /path", mesmo formato usado em toda a base —
    # ver api_analysis_engine._endpoint_source_label) — nunca um dado novo
    # inventado, só a mesma informação já presente em `endpoint`, separada
    # para permitir filtrar/agrupar warnings por método sem reparsear a
    # string em cada consumidor.
    method: str | None = None
    # Onde no request/response o problema ocorreu — mesmo vocabulário já
    # usado por UnresolvedVariable.location (path, base_url, query, header,
    # auth, body, multipart_field) mais "method" para o próprio verbo HTTP;
    # None quando o warning não é localizável num campo específico (ex.:
    # colisão de nome de arquivo, que é uma preocupação de suíte, não de
    # request).
    location: str | None = None
    # Contexto adicional NÃO sensível (regra 6: nunca token, senha, API key
    # ou valor real de secret) — pares (chave, valor) em vez de dict para
    # manter o dataclass hasháveis/imutável, mesmo padrão já usado por
    # GeneratedEndpointTest.resolved_variables.
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.code not in PLAYWRIGHT_WARNING_CODES:
            raise ValueError(
                f"code {self.code!r} não está registrado em "
                "warning_catalog.PLAYWRIGHT_WARNING_CODES — nenhum warning pode usar um "
                "código de texto livre (regra 1 da Parte 24)."
            )
        if self.method is None and self.endpoint:
            derived_method = self.endpoint.strip().split(" ", 1)[0]
            if derived_method:
                object.__setattr__(self, "method", derived_method)
