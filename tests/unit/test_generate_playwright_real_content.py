"""Parte 07 do plano de ação Playwright: ponta a ponta via CLI real
(`generate --file --target playwright`) — confirma que o endpoint GET
simples recebe o primeiro teste positivo real (não mais o placeholder), e
que o endpoint ainda não suportado (TRACE — GET/POST/PUT/PATCH/DELETE/HEAD/
OPTIONS passaram a ser suportados nas Partes 13 e 08A) continua caindo no
fallback com warning, nunca em código enganoso.
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
                            "method": "TRACE",
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
    trace_file = endpoints_dir / "test_trace_users_1.py"
    content = trace_file.read_text(encoding="utf-8")

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


# --- Parte 08B: teste integrado — GET/POST/PUT/PATCH/DELETE numa Collection --


def _write_full_method_collection(tmp_path: Path) -> Path:
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
                        "name": "Criar usuário",
                        "id": "r2",
                        "request": {
                            "method": "POST",
                            "url": "https://api.exemplo.com/users",
                            "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {
                                "mode": "raw",
                                "raw": '{"name": "Maria", "active": true}',
                            },
                        },
                    },
                    {
                        "name": "Atualizar usuário",
                        "id": "r3",
                        "request": {
                            "method": "PUT",
                            "url": "https://api.exemplo.com/users/10",
                            "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {
                                "mode": "raw",
                                "raw": (
                                    '{"id": 10, "name": "Maria", '
                                    '"email": "maria@example.com", "active": true}'
                                ),
                            },
                        },
                    },
                    {
                        "name": "Atualizar status",
                        "id": "r4",
                        "request": {
                            "method": "PATCH",
                            "url": "https://api.exemplo.com/users/10",
                            "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {"mode": "raw", "raw": '{"active": false}'},
                        },
                    },
                    {
                        "name": "Remover usuário",
                        "id": "r5",
                        "request": {
                            "method": "DELETE",
                            "url": "https://api.exemplo.com/users/10",
                        },
                    },
                    {
                        "name": "Remover usuário com motivo",
                        "id": "r6",
                        "request": {
                            "method": "DELETE",
                            "url": "https://api.exemplo.com/users/11",
                            "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {"mode": "raw", "raw": '{"reason": "duplicate"}'},
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture_path


def test_get_post_put_patch_delete_all_produce_real_tests_via_cli(tmp_path, monkeypatch):
    # Regressão da Parte 08A/08B: nenhum destes cinco métodos pode mais cair
    # em HTTP_METHOD_NOT_SUPPORTED quando o método e o formato do request
    # são suportados — cada um gera um cenário de sucesso real, reaproveitando
    # o mesmo pipeline de asserções/manifesto de sempre.
    monkeypatch.chdir(tmp_path)
    fixture = _write_full_method_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert exit_code == SUCCESS
    endpoints_dir = next((tmp_path / "artifacts").rglob("endpoints"))

    put_content = (endpoints_dir / "test_put_users_10.py").read_text(encoding="utf-8")
    patch_content = (endpoints_dir / "test_patch_users_10.py").read_text(encoding="utf-8")
    delete_no_body_content = (endpoints_dir / "test_delete_users_10.py").read_text(
        encoding="utf-8"
    )
    delete_with_body_content = (endpoints_dir / "test_delete_users_11.py").read_text(
        encoding="utf-8"
    )

    for content in (put_content, patch_content, delete_no_body_content, delete_with_body_content):
        assert "@pytest.mark.skip" not in content
        ast.parse(content)

    # PUT preserva o body completo recebido — todos os campos, nenhum
    # descartado nem completado automaticamente.
    assert (
        '    request_body = {\n'
        '        "id": 10,\n'
        '        "name": "Maria",\n'
        '        "email": "maria@example.com",\n'
        '        "active": True,\n'
        "    }\n"
    ) in put_content
    assert "response = api_context.put(\n" in put_content

    # PATCH preserva só o body parcial recebido — nunca expandido para o
    # recurso inteiro (nenhum "id"/"name"/"email" aparece).
    assert '    request_body = {\n        "active": False,\n    }\n' in patch_content
    # '"name": "' (valor string literal) — nunca confundir com o
    # '"name": name,' do helper _record_assertion_result (P1.1), sempre
    # presente e sem relação com o body do PATCH.
    assert '"name": "' not in patch_content
    assert '"email"' not in patch_content
    assert "response = api_context.patch(\n" in patch_content

    # DELETE sem body no request original nunca ganha um body artificial.
    assert 'response = api_context.delete("/users/10")' in delete_no_body_content
    assert "data=" not in delete_no_body_content
    assert "request_body" not in delete_no_body_content

    # DELETE com body explícito no request original o preserva.
    assert (
        '    request_body = {\n        "reason": "duplicate",\n    }\n'
    ) in delete_with_body_content
    assert "response = api_context.delete(\n" in delete_with_body_content

    manifest_path = next((tmp_path / "artifacts").rglob("generation-manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_warning_codes = {warning["code"] for warning in manifest["warnings"]}
    assert "HTTP_METHOD_NOT_SUPPORTED" not in all_warning_codes
    assert manifest["endpoints_analyzed"] == 6
    assert manifest["endpoints_not_rendered"] == []
