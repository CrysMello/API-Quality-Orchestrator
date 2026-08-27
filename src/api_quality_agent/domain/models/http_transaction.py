from dataclasses import dataclass


@dataclass(frozen=True)
class HttpTransactionHeader:
    # Header observado numa transação HTTP real (request ou response) — NUNCA
    # confundir com NormalizedHeader (Postman, tempo de geração, key/value
    # opcionais/desabilitável). Aqui é sempre evidência de execução: nome e
    # valor sempre presentes (o que de fato foi enviado/recebido).
    name: str
    value: str


@dataclass(frozen=True)
class HttpTransaction:
    # Evidência de UMA chamada HTTP feita através do api_context durante a
    # execução da suíte Playwright (P1.2) — nunca inclui o valor real de um
    # secret conhecido (mascarado por PlaywrightAdapter antes de chegar
    # aqui, nunca pelo conftest.py gerado, que não tem — e não deve ter —
    # conhecimento do que é secret).
    #
    # request_headers só reflete o que foi passado explicitamente no
    # call site (headers={...} em api_context.<método>(...)) — Playwright
    # APIRequestContext não expõe a requisição efetivamente enviada (headers
    # compartilhados do contexto incluídos) a partir do APIResponse, então
    # não há como capturar o merge completo sem reimplementar a camada de
    # rede do Playwright.
    method: str
    url: str
    request_headers: tuple[HttpTransactionHeader, ...]
    request_body: str | None
    response_status: int
    response_headers: tuple[HttpTransactionHeader, ...]
    response_body: str | None
    # P1.1 (detalhamento de assertions): mesma chave usada por
    # AssertionResult.test_id — permite reconstruir test_id -> request ->
    # response -> assertions. Default "" (nunca None: sempre uma string,
    # mesmo quando desconhecido) preserva toda construção existente da P1.2
    # que nunca passava isto.
    test_id: str = ""
    # P2.1 (evidência HTTP): query parameters passados explicitamente via
    # `params={...}` no call site gerado (ver playwright_endpoint_test_
    # generator.py/_build_query_params) — nunca derivados/reparseados a
    # partir de `url` (isso inventaria estrutura que não foi de fato
    # observada; `url` já contém a query string real devolvida pelo
    # Playwright, os dois convivem sem se substituir). Mesmo formato de
    # HttpTransactionHeader (par nome/valor) por não haver nada específico
    # de header nesse par — não duplicado como um novo dataclass. Default
    # () preserva toda construção existente anterior ao P2.1.
    query_parameters: tuple[HttpTransactionHeader, ...] = ()
