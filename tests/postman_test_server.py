import http.server
import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit


@dataclass
class _Route:
    status: int
    body: Any
    delay: float = 0.0
    extra_headers: dict[str, str] | None = None


class _RoutedRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (nome exigido pela stdlib)
        self._handle("GET")

    def do_PUT(self) -> None:  # noqa: N802 (nome exigido pela stdlib)
        self._handle("PUT")

    def do_POST(self) -> None:  # noqa: N802 (nome exigido pela stdlib)
        # Dependências entre endpoints (endpoint_dependency_linking.py):
        # reaproveitado por várias suítes de teste novas (produtor real —
        # "criar" é sempre POST na convenção REST), nunca infraestrutura
        # de uso único (ver DESVIO DOCUMENTADO em
        # test_playwright_literal_secret_e2e.py, que evitou isso quando só
        # um teste precisaria).
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802 (nome exigido pela stdlib)
        # P3.3 — múltiplos consumidores (GET/PUT/DELETE) do mesmo valor
        # produzido: sem isto, BaseHTTPRequestHandler responde 501 por
        # conta própria (nunca chega a este handler), o que confundia um
        # "método não suportado pela infraestrutura de teste" com uma
        # falha real da feature de dependências. Mesmo tratamento
        # (equivalente) que GET/POST/PUT já recebem — nunca um segundo
        # mecanismo de rota/captura.
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        server: "PostmanTestServer" = self.server.test_server  # type: ignore[attr-defined]
        server.received_paths.append(self.path)
        server.received_methods.append(method)
        server.received_headers.append(dict(self.headers))
        # path e query são sempre correlacionados por posição (mesmo
        # índice em received_paths/received_queries) com o restante —
        # nunca uma segunda lista de correlação por request_id.
        split_path = urlsplit(self.path)
        server.received_queries.append(dict(parse_qsl(split_path.query)))

        if method in ("PUT", "POST", "DELETE"):
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw_body = self.rfile.read(length) if length else b""
            try:
                server.received_bodies.append(json.loads(raw_body) if raw_body else None)
            except json.JSONDecodeError:
                server.received_bodies.append(raw_body.decode("utf-8", errors="replace"))
        else:
            # Mesma correlação por posição de received_bodies com as
            # demais listas — GET nunca tem corpo, mas a lista precisa
            # continuar alinhada por índice.
            server.received_bodies.append(None)

        route = server.routes.get((method, self.path))
        if route is None:
            self._write(404, {"error": "rota não configurada no servidor de teste", "path": self.path})
            return

        if route.delay:
            time.sleep(route.delay)
        self._write(route.status, route.body, extra_headers=route.extra_headers)

    def _write(
        self, status: int, body: Any, *, extra_headers: dict[str, str] | None = None
    ) -> None:
        if isinstance(body, (bytes, bytearray)):
            payload = bytes(body)
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        else:
            payload = json.dumps(body).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # silencia o log padrão do servidor durante os testes


class _SilentHTTPServer(http.server.HTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        # Em testes de timeout, o cliente abandona a conexão antes do servidor
        # terminar de escrever a resposta atrasada; isso é esperado e não deve
        # poluir a saída dos testes com um traceback.
        pass


class PostmanTestServer:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], _Route] = {}
        self.received_paths: list[str] = []
        self.received_methods: list[str] = []
        self.received_headers: list[dict[str, str]] = []
        self.received_bodies: list[Any] = []
        self.received_queries: list[dict[str, str]] = []
        self._httpd = _SilentHTTPServer(("127.0.0.1", 0), _RoutedRequestHandler)
        self._httpd.test_server = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://127.0.0.1:{port}"

    def set_route(
        self,
        path: str,
        *,
        method: str = "GET",
        status: int = 200,
        body: Any = None,
        delay: float = 0.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.routes[(method, path)] = _Route(
            status=status,
            body=body if body is not None else {},
            delay=delay,
            extra_headers=extra_headers,
        )

    def set_raw_route(
        self, path: str, *, method: str = "GET", status: int = 200, raw_body: str
    ) -> None:
        self.routes[(method, path)] = _Route(status=status, body=raw_body)

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
