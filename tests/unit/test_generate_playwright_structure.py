"""Parte 06 do plano de ação Playwright: estrutura física e persistência da
suíte, exercitada via CLI real (`generate --file --target ...`).

Critérios de aceite cobertos explicitamente:
- --target postman não cria a pasta Playwright;
- --target playwright não cria a pasta Postman;
- --target all cria as duas;
- uma execução não sobrescreve outra;
- os caminhos persistidos existem.
"""

import ast
import json
from pathlib import Path

from api_quality_agent.cli.exit_codes import SUCCESS
from api_quality_agent.cli.main import main


def _write_offline_collection(tmp_path: Path, *, name: str = "Col") -> Path:
    fixture_path = tmp_path / f"{name}.json"
    fixture_path.write_text(
        json.dumps(
            {
                "info": {
                    "name": name,
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": [
                    {
                        "name": "R1",
                        "id": "r1",
                        "request": {"method": "GET", "url": "https://x/y"},
                    },
                    {
                        "name": "R2",
                        "id": "r2",
                        "request": {"method": "POST", "url": "https://x/y"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture_path


def _playwright_dirs(artifacts_root: Path) -> list[Path]:
    return list(artifacts_root.rglob("playwright"))


def _postman_script_files(artifacts_root: Path) -> list[Path]:
    return list(artifacts_root.rglob("*.js"))


def test_target_postman_does_not_create_the_playwright_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "postman"])

    assert exit_code == SUCCESS
    artifacts_root = tmp_path / "artifacts"
    assert _postman_script_files(artifacts_root)
    assert _playwright_dirs(artifacts_root) == []


def test_target_playwright_does_not_create_the_postman_scripts_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert exit_code == SUCCESS
    artifacts_root = tmp_path / "artifacts"
    assert _postman_script_files(artifacts_root) == []
    assert _playwright_dirs(artifacts_root)


def test_target_all_creates_both_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "all"])

    assert exit_code == SUCCESS
    artifacts_root = tmp_path / "artifacts"
    assert _postman_script_files(artifacts_root)
    assert _playwright_dirs(artifacts_root)


def test_two_generate_playwright_runs_do_not_overwrite_each_other(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    first_exit = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])
    second_exit = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert first_exit == SUCCESS
    assert second_exit == SUCCESS

    manifests = list((tmp_path / "artifacts").rglob("generation-manifest.json"))
    assert len(manifests) == 2

    # execution_id (avô do manifesto: .../{execution_id}/scripts/playwright/manifest)
    # é diferente em cada execução — nenhuma sobrescreveu a outra.
    execution_ids = {manifest.parents[2].name for manifest in manifests}
    assert len(execution_ids) == 2


def test_persisted_playwright_paths_actually_exist_with_valid_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    artifacts_root = tmp_path / "artifacts"
    playwright_dir = _playwright_dirs(artifacts_root)[0]

    conftest_path = playwright_dir / "conftest.py"
    manifest_path = playwright_dir / "generation-manifest.json"
    endpoints_dir = playwright_dir / "endpoints"

    assert conftest_path.exists()
    assert manifest_path.exists()
    assert endpoints_dir.is_dir()

    ast.parse(conftest_path.read_text(encoding="utf-8"))
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_payload["endpoints_analyzed"] == 2

    endpoint_files = list(endpoints_dir.glob("*.py"))
    assert len(endpoint_files) == 2
    for endpoint_file in endpoint_files:
        ast.parse(endpoint_file.read_text(encoding="utf-8"))


def test_playwright_and_postman_artifacts_for_the_same_run_do_not_collide(tmp_path, monkeypatch):
    # target=all: os dois lados escrevem sob a mesma árvore artifacts/, com
    # execution_ids independentes (Parte 06) — confirma que isso nunca
    # resulta em um arquivo de um lado sobrescrevendo o do outro.
    monkeypatch.chdir(tmp_path)
    fixture = _write_offline_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "all"])

    assert exit_code == SUCCESS
    artifacts_root = tmp_path / "artifacts"
    js_files_before = set(_postman_script_files(artifacts_root))
    playwright_files_before = set(artifacts_root.rglob("*.py"))

    assert js_files_before
    assert playwright_files_before
    assert js_files_before.isdisjoint(playwright_files_before)
