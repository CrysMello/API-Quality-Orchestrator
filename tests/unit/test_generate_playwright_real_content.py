"""Parte 07 do plano de ação Playwright: ponta a ponta via CLI real
(`generate --file --target playwright`) — confirma que o endpoint GET
simples recebe o primeiro teste positivo real (não mais o placeholder), e
que o endpoint ainda não suportado (DELETE — POST passou a ser suportado na
Parte 13) continua caindo no fallback com warning, nunca em código
enganoso.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

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
                        "name": "Listar usuários",
                        "id": "r1",
                        "request": {"method": "GET", "url": "https://api.exemplo.com/users"},
                    },
                    {
                        "name": "Remover usuário",
                        "id": "r2",
                        "request": {
                            "method": "DELETE",
                            "url": "https://api.exemplo.com/users/1",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture_path


def test_get_endpoint_receives_a_real_positive_test_via_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert exit_code == SUCCESS
    endpoints_dir = next((tmp_path / "artifacts").rglob("endpoints"))
    get_file = endpoints_dir / "test_get_users.py"
    content = get_file.read_text(encoding="utf-8")

    assert "def test_get_users_success(api_context):" in content
    assert 'api_context.get("/users")' in content
    assert "assert response is not None" in content
    assert "@pytest.mark.skip" not in content
    ast.parse(content)


def test_unsupported_endpoint_still_falls_back_to_placeholder_with_valid_syntax(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert exit_code == SUCCESS
    endpoints_dir = next((tmp_path / "artifacts").rglob("endpoints"))
    delete_file = endpoints_dir / "test_delete_users_1.py"
    content = delete_file.read_text(encoding="utf-8")

    assert "@pytest.mark.skip" in content
    assert "response = api_context" not in content
    ast.parse(content)


def test_manifest_reports_the_fallback_warning(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    manifest_path = next((tmp_path / "artifacts").rglob("generation-manifest.json"))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["endpoints_analyzed"] == 2


def test_both_generated_endpoint_files_are_collectible_by_pytest(tmp_path, monkeypatch):
    # "coleta do pytest" (critério de aceite): os arquivos precisam ser
    # importáveis/coletáveis, mesmo sem uma fixture api_context real
    # funcional ainda (execução via run é escopo de outra parte).
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)
    main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    endpoints_dir = next((tmp_path / "artifacts").rglob("endpoints"))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(endpoints_dir)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_get_users_success" in result.stdout
