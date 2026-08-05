from dataclasses import dataclass


@dataclass(frozen=True)
class PlaywrightGenerationWarning:
    # Equivalente, para o gerador Playwright, ao `GenerationWarning` já
    # existente em generators/generation_warning.py (usado pelo
    # PostmanTestGenerator) — não é o mesmo tipo porque o formato de
    # rastreabilidade é diferente: aqui um warning é sempre vinculado a um
    # endpoint e a um cenário (nunca só a um test_id/field JS), conforme o
    # formato do manifesto de geração (plano de ação Playwright, seção 7).
    code: str
    message: str
    endpoint: str | None
    scenario: str | None
