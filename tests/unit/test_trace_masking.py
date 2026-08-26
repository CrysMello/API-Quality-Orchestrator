"""P1.3 (Trace em falha) — mascaramento de um Playwright Trace (.zip) antes
de qualquer persistência (adapters/playwright/trace_masking.py).

O formato exercitado aqui (trace.trace/trace.network NDJSON, trace.stacks,
resources/*.bin|*.json) foi medido empiricamente contra o Playwright 1.61
(a versão instalada neste projeto) — ver docstring do próprio módulo.
"""

import base64
import json
import zipfile
from pathlib import Path

from api_quality_agent.adapters.playwright.trace_masking import mask_trace_archive


def _write_zip(path: Path, members: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for name, content in members.items():
            zip_file.writestr(name, content)


def _read_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zip_file:
        return {name: zip_file.read(name) for name in zip_file.namelist()}


def _mask(tmp_path: Path, members: dict[str, bytes | str], known_secret_values=()):
    source = tmp_path / "source.zip"
    destination = tmp_path / "destination.zip"
    _write_zip(source, members)
    mask_trace_archive(
        source_path=source, destination_path=destination, known_secret_values=known_secret_values
    )
    return _read_zip(destination)


# --- Redação estrutural por NOME de header/cookie (nunca por valor conhecido) --


def test_authorization_header_value_is_redacted_regardless_of_known_secret_values(tmp_path):
    line = json.dumps(
        {
            "type": "before",
            "params": {"headers": [{"name": "Authorization", "value": "Bearer dyn-token-xyz"}]},
        }
    )
    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=())

    text = result["trace.trace"].decode("utf-8")
    assert "dyn-token-xyz" not in text
    assert "[REDACTED]" in text


def test_cookie_header_value_is_redacted(tmp_path):
    line = json.dumps(
        {
            "type": "resource-snapshot",
            "snapshot": {
                "request": {"headers": [{"name": "Cookie", "value": "session=abc123"}]},
            },
        }
    )
    result = _mask(tmp_path, {"trace.network": line + "\n"}, known_secret_values=())

    text = result["trace.network"].decode("utf-8")
    assert "abc123" not in text
    assert "[REDACTED]" in text


def test_cookies_array_values_are_redacted(tmp_path):
    line = json.dumps(
        {
            "type": "resource-snapshot",
            "snapshot": {"request": {"cookies": [{"name": "session", "value": "abc123secret"}]}},
        }
    )
    result = _mask(tmp_path, {"trace.network": line + "\n"}, known_secret_values=())

    assert "abc123secret" not in result["trace.network"].decode("utf-8")


def test_set_cookie_response_header_value_is_redacted(tmp_path):
    line = json.dumps(
        {
            "type": "resource-snapshot",
            "snapshot": {
                "response": {
                    "headers": [{"name": "Set-Cookie", "value": "session=abc123; Secure"}]
                },
            },
        }
    )
    result = _mask(tmp_path, {"trace.network": line + "\n"}, known_secret_values=())

    text = result["trace.network"].decode("utf-8")
    assert "abc123" not in text
    assert "[REDACTED]" in text


def test_proxy_authorization_header_value_is_redacted(tmp_path):
    line = json.dumps(
        {
            "type": "before",
            "params": {"headers": [{"name": "Proxy-Authorization", "value": "Basic dGVzdDoxMjM="}]},
        }
    )
    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=())

    text = result["trace.trace"].decode("utf-8")
    assert "dGVzdDoxMjM=" not in text
    assert "[REDACTED]" in text


def test_non_sensitive_headers_are_never_redacted(tmp_path):
    line = json.dumps(
        {
            "type": "before",
            "params": {"headers": [{"name": "content-type", "value": "application/json"}]},
        }
    )
    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=())

    text = result["trace.trace"].decode("utf-8")
    assert "application/json" in text
    assert "[REDACTED]" not in text


def test_authorization_mentioned_in_a_free_text_log_message_is_also_redacted(tmp_path):
    # trace.trace duplica o mesmo header em eventos "log" de texto livre —
    # a redação estrutural do array "headers" não alcança essa duplicata.
    line = json.dumps({"type": "log", "message": "  Authorization: Bearer dyn-token-xyz"})
    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=())

    text = result["trace.trace"].decode("utf-8")
    assert "dyn-token-xyz" not in text
    assert "[REDACTED]" in text
    assert "Authorization:" in text  # o NOME do header continua visível


def test_unrelated_log_messages_are_preserved(tmp_path):
    line = json.dumps({"type": "log", "message": "→ POST http://api.exemplo.com/users"})
    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=())

    assert "POST http://api.exemplo.com/users" in result["trace.trace"].decode("utf-8")


# --- postData em base64 (trace.trace) ---------------------------------------


def test_base64_post_data_is_decoded_masked_and_reencoded(tmp_path):
    body = json.dumps({"password": "hunter2"})
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    line = json.dumps({"type": "before", "params": {"postData": encoded}})

    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=("hunter2",))

    payload = json.loads(result["trace.trace"].decode("utf-8"))
    decoded = base64.b64decode(payload["params"]["postData"]).decode("utf-8")
    assert "hunter2" not in decoded
    assert json.loads(decoded)["password"] != "hunter2"


def test_base64_post_data_without_a_known_secret_is_preserved_as_is(tmp_path):
    # Limitação documentada e aceita: um valor de negócio comum (não
    # cadastrado como secret) não é mascarado — mesmo piso de segurança já
    # aceito para HttpTransaction/AssertionResult.
    body = json.dumps({"role": "admin"})
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    line = json.dumps({"type": "before", "params": {"postData": encoded}})

    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=())

    payload = json.loads(result["trace.trace"].decode("utf-8"))
    decoded = base64.b64decode(payload["params"]["postData"]).decode("utf-8")
    assert json.loads(decoded) == {"role": "admin"}


def test_non_utf8_base64_post_data_is_stripped_not_left_unverified(tmp_path):
    binary_body = bytes([0xFF, 0xFE, 0x00, 0x01])
    encoded = base64.b64encode(binary_body).decode("ascii")
    line = json.dumps({"type": "before", "params": {"postData": encoded}})

    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=())

    payload = json.loads(result["trace.trace"].decode("utf-8"))
    decoded = base64.b64decode(payload["params"]["postData"])
    assert decoded != binary_body
    assert b"omitted" in decoded


def test_invalid_base64_post_data_never_crashes_the_export(tmp_path):
    line = json.dumps({"type": "before", "params": {"postData": "not-actually-base64!!"}})

    result = _mask(tmp_path, {"trace.trace": line + "\n"}, known_secret_values=())

    assert result["trace.trace"]  # nunca levanta, arquivo exportado normalmente


# --- Recursos (resources/*) --------------------------------------------------


def test_text_resource_is_masked_by_known_secret_value(tmp_path):
    # Corpo de REQUEST (via resources/*.bin, formato real do Playwright).
    result = _mask(
        tmp_path,
        {"resources/abc.bin": json.dumps({"password": "hunter2"})},
        known_secret_values=("hunter2",),
    )

    assert b"hunter2" not in result["resources/abc.bin"]


def test_response_body_resource_is_masked_by_known_secret_value(tmp_path):
    # Corpo de RESPONSE (via resources/*.json, referenciado por
    # response.content._sha1 em trace.network) — mesmo mecanismo genérico
    # de _mask_resource_member, exercitado aqui explicitamente para a
    # direção de resposta (item 12 do bloco de hardening).
    result = _mask(
        tmp_path,
        {"resources/response.json": json.dumps({"token": "sk_live_super_secret_e2e"})},
        known_secret_values=("sk_live_super_secret_e2e",),
    )

    assert b"sk_live_super_secret_e2e" not in result["resources/response.json"]


def test_genuinely_binary_resource_is_stripped_not_left_unverified(tmp_path):
    binary_content = bytes([0xFF, 0xFE, 0xFD, 0x00, 0x01, 0x02])
    result = _mask(tmp_path, {"resources/image.bin": binary_content}, known_secret_values=())

    assert result["resources/image.bin"] != binary_content
    assert b"omitted" in result["resources/image.bin"]


def test_text_resource_without_any_secret_is_preserved_unchanged(tmp_path):
    result = _mask(
        tmp_path, {"resources/ok.json": '{"id": "u-1"}'}, known_secret_values=("unrelated",)
    )

    assert result["resources/ok.json"] == b'{"id": "u-1"}'


# --- trace.stacks -------------------------------------------------------------


def test_trace_stacks_is_preserved_when_it_has_no_secret(tmp_path):
    content = json.dumps({"files": ["<stdin>"], "stacks": [[1, [[0, 1, 0, "<module>"]]]]})
    result = _mask(tmp_path, {"trace.stacks": content}, known_secret_values=())

    assert result["trace.stacks"].decode("utf-8") == content


# --- Linhas malformadas nunca derrubam a exportação inteira -----------------


def test_malformed_json_line_falls_back_to_generic_masking(tmp_path):
    result = _mask(
        tmp_path,
        {"trace.trace": "isto não é um JSON válido, mas contém hunter2\n"},
        known_secret_values=("hunter2",),
    )

    text = result["trace.trace"].decode("utf-8")
    assert "hunter2" not in text


def test_empty_lines_are_preserved_without_crashing(tmp_path):
    result = _mask(tmp_path, {"trace.trace": "\n\n"}, known_secret_values=())

    assert result["trace.trace"].decode("utf-8") == "\n\n"


# --- Nunca modifica o .zip de origem -----------------------------------------


def test_source_archive_is_never_modified(tmp_path):
    source = tmp_path / "source.zip"
    destination = tmp_path / "destination.zip"
    original_line = json.dumps({"type": "log", "message": "Authorization: Bearer secret-token"})
    _write_zip(source, {"trace.trace": original_line + "\n"})
    original_bytes = source.read_bytes()

    mask_trace_archive(
        source_path=source, destination_path=destination, known_secret_values=("secret-token",)
    )

    assert source.read_bytes() == original_bytes
