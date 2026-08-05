"""Parte 04 do plano de ação Playwright: roteamento de `generate --target
{postman,playwright,all}`. Usa o caminho `--file` (offline, sem rede) para
exercitar o `cli.main.main(...)` real de ponta a ponta sem precisar de um
servidor Postman simulado — o roteamento em si é agnóstico da origem do
documento (mesmo helper `_generate_for_target` nos três caminhos).
"""

import json
from pathlib import Path

import pytest

from api_quality_agent.cli.exit_codes import FUNCTIONAL_FAILURE, SUCCESS
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
                        "request": {"method": "GET", "url": "https://x/y"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture_path


def test_default_target_behaves_exactly_like_postman(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y"])

    assert exit_code == SUCCESS
    assert "Processo concluído com sucesso." in capsys.readouterr().out
    assert any((tmp_path / "artifacts").rglob("*.js"))


def test_explicit_target_postman_behaves_the_same_as_default(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "postman"])

    assert exit_code == SUCCESS
    assert "Processo concluído com sucesso." in capsys.readouterr().out
    assert any((tmp_path / "artifacts").rglob("*.js"))


def test_target_playwright_returns_not_implemented_and_never_touches_postman(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert exit_code == FUNCTIONAL_FAILURE
    assert "PLAYWRIGHT_GENERATION_NOT_IMPLEMENTED" in capsys.readouterr().err
    # playwright não chama postman: nenhum artefato Postman foi salvo.
    assert not (tmp_path / "artifacts").exists()


def test_target_all_runs_postman_then_fails_on_the_playwright_stub(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "all"])

    assert exit_code == FUNCTIONAL_FAILURE
    assert "PLAYWRIGHT_GENERATION_NOT_IMPLEMENTED" in capsys.readouterr().err
    # all prepara a chamada dos dois: o lado Postman já funciona e salvou
    # artefatos normalmente, mesmo com a falha (esperada) do lado Playwright.
    assert any((tmp_path / "artifacts").rglob("*.js"))


def test_invalid_target_value_is_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["generate", "--file", str(fixture), "-y", "--target", "invalido"])

    assert exc_info.value.code == 2
