"""Roteamento de `generate --target {postman,playwright,all}` (Parte 04),
atualizado na Parte 06 quando o lado Playwright deixou de ser um stub que
falha (`PLAYWRIGHT_GENERATION_NOT_IMPLEMENTED`) e passou a gerar/persistir
a estrutura real da suíte (ainda com conteúdo de endpoint mínimo/placeholder).

Usa o caminho `--file` (offline, sem rede) para exercitar o
`cli.main.main(...)` real de ponta a ponta — o roteamento em si é agnóstico
da origem do documento (mesmo helper `_generate_for_target` nos três
caminhos).
"""

import json
from pathlib import Path

import pytest

from api_quality_agent.cli.exit_codes import SUCCESS
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
    assert not any((tmp_path / "artifacts").rglob("conftest.py"))


def test_explicit_target_postman_behaves_the_same_as_default(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "postman"])

    assert exit_code == SUCCESS
    assert "Processo concluído com sucesso." in capsys.readouterr().out
    assert any((tmp_path / "artifacts").rglob("*.js"))
    assert not any((tmp_path / "artifacts").rglob("conftest.py"))


def test_target_playwright_generates_the_suite_without_touching_postman(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert exit_code == SUCCESS
    out = capsys.readouterr().out
    assert "Processo concluído com sucesso." in out
    assert "Playwright:" in out
    assert "Postman:" not in out
    # playwright não chama postman: nenhum .js foi gerado.
    assert not any((tmp_path / "artifacts").rglob("*.js"))
    assert any((tmp_path / "artifacts").rglob("conftest.py"))


def test_target_all_generates_both(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "all"])

    assert exit_code == SUCCESS
    out = capsys.readouterr().out
    assert "Postman:" in out
    assert "Playwright:" in out
    assert any((tmp_path / "artifacts").rglob("*.js"))
    assert any((tmp_path / "artifacts").rglob("conftest.py"))


def test_invalid_target_value_is_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["generate", "--file", str(fixture), "-y", "--target", "invalido"])

    assert exc_info.value.code == 2
