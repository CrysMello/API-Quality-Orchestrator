"""Fake mínimo de playwright.sync_api — nunca a dependência real (mesmo
princípio de tests/fake_newman.py, aplicado a um pacote importado em vez de
um binário invocado por subprocess). Existe só para provar, sem precisar do
pacote playwright de verdade instalado neste projeto, que o conftest.py
gerado (Parte 08) é importável/coletável pelo pytest e que a fixture
api_context de fato descarta o contexto no teardown.
"""

from __future__ import annotations

from typing import Any

CREATED_CONTEXTS: list["APIRequestContext"] = []
DISPOSED_CONTEXTS: list["APIRequestContext"] = []


def reset_state() -> None:
    CREATED_CONTEXTS.clear()
    DISPOSED_CONTEXTS.clear()


class APIRequestContext:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url
        self.disposed = False

    def get(self, url: str, **kwargs: Any) -> "_FakeResponse":
        return _FakeResponse()

    def dispose(self) -> None:
        self.disposed = True
        DISPOSED_CONTEXTS.append(self)


class _FakeResponse:
    ok = True


class _RequestNamespace:
    def new_context(self, *, base_url: str | None = None, **kwargs: Any) -> APIRequestContext:
        context = APIRequestContext(base_url=base_url)
        CREATED_CONTEXTS.append(context)
        return context


class _Playwright:
    def __init__(self) -> None:
        self.request = _RequestNamespace()


class _PlaywrightContextManager:
    def __enter__(self) -> _Playwright:
        return _Playwright()

    def __exit__(self, *exc_info: object) -> bool:
        return False


def sync_playwright() -> _PlaywrightContextManager:
    return _PlaywrightContextManager()
