"""Parte 09 do plano de ação Playwright: `-e`/`--environment` no comando
`generate`, ponta a ponta via CLI real.
"""

import json
from pathlib import Path

from api_quality_agent.cli.exit_codes import INVALID_INPUT_OR_CONFIGURATION, SUCCESS
from api_quality_agent.cli.main import main


def _write_offline_collection(tmp_path: Path) -> Path:
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
                        "name": "R1",
                        "id": "r1",
                        "request": {"method": "GET", "url": "https://api.exemplo.com/users"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_environment(tmp_path: Path, *, name: str = "env.json", payload: dict | None = None) -> Path:
    env_path = tmp_path / name
    default_payload = {
        "name": "QA",
        "values": [
            {"key": "baseUrl", "value": "https://api.exemplo.com", "type": "default", "enabled": True},
            {"key": "apiKey", "value": "segredo-nao-pode-vazar", "type": "secret", "enabled": True},
        ],
    }
    env_path.write_text(json.dumps(payload or default_payload), encoding="utf-8")
    return env_path


# --- Parâmetros funcionam ---------------------------------------------------


def test_environment_flag_works_with_target_playwright(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    collection = _write_offline_collection(tmp_path)
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
    assert "Playwright:" in capsys.readouterr().out


def test_environment_short_flag_is_equivalent_to_long_form(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    collection = _write_offline_collection(tmp_path)
    environment = _write_environment(tmp_path)

    exit_code = main(
        [
            "generate",
            "--file",
            str(collection),
            "-y",
            "--target",
            "playwright",
            "--environment",
            str(environment),
        ]
    )

    assert exit_code == SUCCESS


def test_environment_is_optional_generate_still_works_without_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    collection = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(collection), "-y", "--target", "playwright"])

    assert exit_code == SUCCESS


# --- Compatível com Postman: --target postman também aceita -e -----------


def test_environment_flag_does_not_break_target_postman(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    collection = _write_offline_collection(tmp_path)
    environment = _write_environment(tmp_path)

    exit_code = main(
        ["generate", "--file", str(collection), "-y", "-e", str(environment)]
    )  # target padrão = postman

    assert exit_code == SUCCESS
    assert "Postman:" in capsys.readouterr().out
    assert any((tmp_path / "artifacts").rglob("*.js"))


def test_environment_secret_value_never_appears_in_generated_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    collection = _write_offline_collection(tmp_path)
    environment = _write_environment(tmp_path)

    main(
        [
            "generate",
            "--file",
            str(collection),
            "-y",
            "--target",
            "all",
            "-e",
            str(environment),
        ]
    )

    for generated_file in (tmp_path / "artifacts").rglob("*"):
        if generated_file.is_file():
            content = generated_file.read_text(encoding="utf-8")
            assert "segredo-nao-pode-vazar" not in content


# --- Erros claros ------------------------------------------------------------


def test_missing_environment_file_gives_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    collection = _write_offline_collection(tmp_path)

    exit_code = main(
        [
            "generate",
            "--file",
            str(collection),
            "-y",
            "-e",
            str(tmp_path / "nao-existe.json"),
        ]
    )

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION
    assert "não encontrado" in capsys.readouterr().err.lower()


def test_invalid_json_environment_gives_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    collection = _write_offline_collection(tmp_path)
    bad_env = tmp_path / "bad_env.json"
    bad_env.write_text("{ isto não é json", encoding="utf-8")

    exit_code = main(["generate", "--file", str(collection), "-y", "-e", str(bad_env)])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION
    assert "JSON válido" in capsys.readouterr().err


def test_environment_missing_values_gives_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    collection = _write_offline_collection(tmp_path)
    bad_env = _write_environment(tmp_path, payload={"name": "QA"})

    exit_code = main(["generate", "--file", str(collection), "-y", "-e", str(bad_env)])

    assert exit_code == INVALID_INPUT_OR_CONFIGURATION
    assert "values" in capsys.readouterr().err
