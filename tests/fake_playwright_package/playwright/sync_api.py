"""Fake mínimo de playwright.sync_api — nunca a dependência real (mesmo
princípio de tests/fake_newman.py, aplicado a um pacote importado em vez de
um binário invocado por subprocess). Existe só para provar, sem precisar do
pacote playwright de verdade instalado neste projeto, que o conftest.py
gerado (Parte 08) é importável/coletável pelo pytest e que a fixture
api_context de fato descarta o contexto no teardown.

P1.2: get/post/put/patch/delete/head/fetch ganham uma resposta configurável
(NEXT_RESPONSE) — necessário para testar, contra este mesmo fake, que o
wrapper de captura de transação HTTP (_wrap_for_http_capture, embutido no
conftest.py gerado) de fato intercepta cada método e grava o que aconteceu,
sem depender do pacote playwright real.
"""

from __future__ import annotations

from typing import Any

CREATED_CONTEXTS: list["APIRequestContext"] = []
DISPOSED_CONTEXTS: list["APIRequestContext"] = []
CALLS: list[dict[str, Any]] = []
# P1.4 (hardening): quando definido, dispose() levanta esta exceção em vez
# de descartar normalmente — usado só para provar que uma falha durante
# dispose() nunca esconde a falha original de um teste (ver conftest.py
# gerado, _render_conftest). Resetado a cada reset_state().
DISPOSE_ERROR: Exception | None = None
# Resposta usada pela PRÓXIMA chamada get/post/.../fetch — resetada a cada
# reset_state(). Default cobre o caso comum (200, sem headers extras, corpo
# JSON simples) sem exigir que todo teste configure isto.
NEXT_RESPONSE: dict[str, Any] = {
    "status": 200,
    "headers": {"content-type": "application/json"},
    "body": '{"ok": true}',
}


def reset_state() -> None:
    global DISPOSE_ERROR
    CREATED_CONTEXTS.clear()
    DISPOSED_CONTEXTS.clear()
    CALLS.clear()
    NEXT_RESPONSE.clear()
    NEXT_RESPONSE.update(status=200, headers={"content-type": "application/json"}, body='{"ok": true}')
    DISPOSE_ERROR = None


class APIRequestContext:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url
        self.disposed = False

    def _respond(self, method: str, url: str, kwargs: dict[str, Any]) -> "_FakeResponse":
        CALLS.append({"method": method, "url": url, "kwargs": kwargs})
        return _FakeResponse(
            url=url,
            status=NEXT_RESPONSE["status"],
            headers=NEXT_RESPONSE["headers"],
            body=NEXT_RESPONSE["body"],
        )

    def get(self, url: str, **kwargs: Any) -> "_FakeResponse":
        return self._respond("GET", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> "_FakeResponse":
        return self._respond("POST", url, kwargs)

    def put(self, url: str, **kwargs: Any) -> "_FakeResponse":
        return self._respond("PUT", url, kwargs)

    def patch(self, url: str, **kwargs: Any) -> "_FakeResponse":
        return self._respond("PATCH", url, kwargs)

    def delete(self, url: str, **kwargs: Any) -> "_FakeResponse":
        return self._respond("DELETE", url, kwargs)

    def head(self, url: str, **kwargs: Any) -> "_FakeResponse":
        return self._respond("HEAD", url, kwargs)

    def fetch(self, url: str, **kwargs: Any) -> "_FakeResponse":
        return self._respond(kwargs.get("method", "GET"), url, kwargs)

    def dispose(self) -> None:
        if DISPOSE_ERROR is not None:
            raise DISPOSE_ERROR
        self.disposed = True
        DISPOSED_CONTEXTS.append(self)


class _FakeResponse:
    def __init__(self, *, url: str, status: int, headers: dict[str, str], body: str) -> None:
        self.url = url
        self.status = status
        self.headers = headers
        self._body = body
        self.ok = 200 <= status < 300

    def text(self) -> str:
        return self._body


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
