"""Parte 12 do plano de ação Playwright: ponta a ponta via CLI real —
confirma que nenhum segredo real aparece em nenhum artefato persistido
(arquivos .py por endpoint, conftest.py, generation-manifest.json) nem nos
logs de geração impressos no terminal, mesmo quando o Environment usado
para a geração contém os valores reais das variáveis referenciadas pela
autenticação.
"""

import json
from pathlib import Path

from api_quality_agent.cli.exit_codes import SUCCESS
from api_quality_agent.cli.main import main

# Valores "reais" propositalmente reconhecíveis — se qualquer um destes
# aparecer em qualquer artefato gerado, o teste falha.
_REAL_ACCESS_TOKEN = "sk_live_should_never_leak_9f8e7d6c5b4a"
_REAL_API_KEY = "apikey_should_never_leak_1a2b3c4d5e6f"
_REAL_BASIC_USERNAME = "admin_should_never_leak"
_REAL_BASIC_PASSWORD = "hunter2_should_never_leak"
_REAL_MANUAL_AUTH_HEADER = "Bearer manual-secret-should-never-leak"

_ALL_SECRETS = (
    _REAL_ACCESS_TOKEN,
    _REAL_API_KEY,
    _REAL_BASIC_USERNAME,
    _REAL_BASIC_PASSWORD,
    _REAL_MANUAL_AUTH_HEADER,
)


def _write_collection(tmp_path: Path) -> Path:
    fixture_path = tmp_path / "collection.json"
    fixture_path.write_text(
        json.dumps(
            {
                "info": {
                    "name": "Col",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": [
                    {
                        "name": "Bearer",
                        "id": "r1",
                        "request": {
                            "method": "GET",
                            "url": "https://api.exemplo.com/bearer",
                            "auth": {
                                "type": "bearer",
                                "bearer": [{"key": "token", "value": "{{accessToken}}"}],
                            },
                        },
                    },
                    {
                        "name": "API Key header",
                        "id": "r2",
                        "request": {
                            "method": "GET",
                            "url": "https://api.exemplo.com/apikey-header",
                            "auth": {
                                "type": "apikey",
                                "apikey": [
                                    {"key": "key", "value": "X-API-Key"},
                                    {"key": "value", "value": "{{apiKey}}"},
                                    {"key": "in", "value": "header"},
                                ],
                            },
                        },
                    },
                    {
                        "name": "API Key query",
                        "id": "r3",
                        "request": {
                            "method": "GET",
                            "url": "https://api.exemplo.com/apikey-query",
                            "auth": {
                                "type": "apikey",
                                "apikey": [
                                    {"key": "key", "value": "api_key"},
                                    {"key": "value", "value": "{{apiKey}}"},
                                    {"key": "in", "value": "query"},
                                ],
                            },
                        },
                    },
                    {
                        "name": "Basic",
                        "id": "r4",
                        "request": {
                            "method": "GET",
                            "url": "https://api.exemplo.com/basic",
                            "auth": {
                                "type": "basic",
                                "basic": [
                                    {"key": "username", "value": "{{basicUsername}}"},
                                    {"key": "password", "value": "{{basicPassword}}"},
                                ],
                            },
                        },
                    },
                    {
                        "name": "Manual Authorization header",
                        "id": "r5",
                        "request": {
                            "method": "GET",
                            "url": "https://api.exemplo.com/manual-auth",
                            "header": [
                                {"key": "Authorization", "value": _REAL_MANUAL_AUTH_HEADER}
                            ],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_environment(tmp_path: Path) -> Path:
    env_path = tmp_path / "environment.json"
    env_path.write_text(
        json.dumps(
            {
                "name": "QA",
                "values": [
                    {"key": "accessToken", "value": _REAL_ACCESS_TOKEN, "type": "secret"},
                    {"key": "apiKey", "value": _REAL_API_KEY, "type": "secret"},
                    {"key": "basicUsername", "value": _REAL_BASIC_USERNAME, "type": "secret"},
                    {"key": "basicPassword", "value": _REAL_BASIC_PASSWORD, "type": "secret"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return env_path


def test_real_secret_values_never_appear_in_any_generated_artifact(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    collection = _write_collection(tmp_path)
    environment = _write_environment(tmp_path)

    exit_code = main(
        [
            "generate",
            "--file",
            str(collection),
            "-y",
            "--target",
            "playwright",
            "-e",
            str(environment),
        ]
    )

    assert exit_code == SUCCESS

    # Todo arquivo persistido sob artifacts/ (endpoints/*.py, conftest.py,
    # generation-manifest.json) — nenhum segredo real em nenhum deles.
    artifact_files = [p for p in (tmp_path / "artifacts").rglob("*") if p.is_file()]
    assert len(artifact_files) >= 3  # pelo menos os endpoints + conftest + manifesto

    for artifact_file in artifact_files:
        content = artifact_file.read_text(encoding="utf-8")
        for secret in _ALL_SECRETS:
            assert secret not in content, f"segredo vazou em {artifact_file}"

    # Logs de geração (stdout/stderr impressos pela CLI) — mesmo critério.
    captured = capsys.readouterr()
    for secret in _ALL_SECRETS:
        assert secret not in captured.out, f"segredo vazou no stdout: {secret}"
        assert secret not in captured.err, f"segredo vazou no stderr: {secret}"


def test_bearer_endpoint_file_uses_the_environment_variable_pattern(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    collection = _write_collection(tmp_path)
    environment = _write_environment(tmp_path)

    main(
        [
            "generate",
            "--file",
            str(collection),
            "-y",
            "--target",
            "playwright",
            "-e",
            str(environment),
        ]
    )

    endpoints_dir = next((tmp_path / "artifacts").rglob("endpoints"))
    bearer_file = endpoints_dir / "test_get_bearer.py"
    content = bearer_file.read_text(encoding="utf-8")

    assert 'os.environ.get("AQO_ACCESS_TOKEN")' in content
    assert _REAL_ACCESS_TOKEN not in content
