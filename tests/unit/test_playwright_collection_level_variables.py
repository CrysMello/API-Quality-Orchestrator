"""Correção de gap identificado ao validar a geração Playwright com uma
Collection real (JSONPlaceholder.dev): variáveis declaradas só no array
`variable[]` de NÍVEL DE COLLECTION (ex.: `baseUrl`, usado no host de toda
a Collection) nunca resolviam sem um Environment explícito — o resolvedor
central (variable_resolver.py) só conhecia `url.variable[]` (default por
segmento de path) como "literal já conhecido na geração".

`merge_collection_variables` materializa essas entradas como um Environment
de prioridade mais baixa (nunca sobrescrevendo uma variável de Environment
real) — ver testes unitários da função em test_variable_resolver.py. Este
arquivo prova o efeito ponta a ponta, via CLI real (mesmo padrão de
test_generate_playwright_real_content.py), sem nenhum Environment
informado.
"""

import ast
import json
from pathlib import Path

from api_quality_agent.cli.exit_codes import SUCCESS
from api_quality_agent.cli.main import main


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
                # baseUrl/postId: só declarados aqui, nunca em url.variable[]
                # nem num Environment — exatamente o formato da Collection
                # JSONPlaceholder.dev usada na validação manual. host é só o
                # hostname (sem esquema) e "protocol" é declarado à parte,
                # mesma decomposição usada pelo próprio app do Postman ao
                # exportar uma Collection.
                "variable": [
                    {"key": "baseUrl", "value": "api.exemplo.com", "type": "string"},
                    {"key": "postId", "value": "1", "type": "string"},
                ],
                "item": [
                    {
                        "name": "Get All Users",
                        "id": "r1",
                        "request": {
                            "method": "GET",
                            "url": {
                                "raw": "{{baseUrl}}/users",
                                "protocol": "https",
                                "host": ["{{baseUrl}}"],
                                "path": ["users"],
                            },
                        },
                    },
                    {
                        "name": "Get Post by ID",
                        "id": "r2",
                        "request": {
                            "method": "GET",
                            "url": {
                                "raw": "{{baseUrl}}/posts/{{postId}}",
                                "protocol": "https",
                                "host": ["{{baseUrl}}"],
                                "path": ["posts", "{{postId}}"],
                            },
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture_path


def test_host_variable_declared_only_at_collection_level_resolves_without_an_environment(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    fixture = _write_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert exit_code == SUCCESS
    conftest = next((tmp_path / "artifacts").rglob("conftest.py")).read_text(encoding="utf-8")
    assert '_DEFAULT_BASE_URL = "https://api.exemplo.com"' in conftest

    endpoints_dir = next((tmp_path / "artifacts").rglob("endpoints"))
    get_users = (endpoints_dir / "test_get_users.py").read_text(encoding="utf-8")
    assert "@pytest.mark.skip" not in get_users
    assert 'api_context.get("/users")' in get_users
    ast.parse(get_users)


def test_pure_path_variable_declared_only_at_collection_level_resolves_as_a_literal(
    tmp_path, monkeypatch
):
    # {{postId}} não tem default em url.variable[] (isso é um mecanismo
    # diferente, por segmento) — só o array de nível de Collection.
    monkeypatch.chdir(tmp_path)
    fixture = _write_collection(tmp_path)

    exit_code = main(["generate", "--file", str(fixture), "-y", "--target", "playwright"])

    assert exit_code == SUCCESS
    endpoints_dir = next((tmp_path / "artifacts").rglob("endpoints"))
    get_post = next(
        f for f in endpoints_dir.glob("*.py") if "post" in f.name and "user" not in f.name
    ).read_text(encoding="utf-8")

    assert "@pytest.mark.skip" not in get_post
    assert 'api_context.get("/posts/1")' in get_post
    # Nunca deferido para uma variável de ambiente do sistema (regra: só
    # prioridades 1/2 resolvem path em tempo de geração) — "AQO_" é o
    # prefixo exclusivo de variável deferida (variable_resolver.py); não
    # confundir com o os.environ.get incondicional de
    # PLAYWRIGHT_ASSERTION_RESULTS_PATH (P1.1).
    assert 'os.environ.get("AQO_' not in get_post
    ast.parse(get_post)


def test_an_explicit_environment_still_wins_over_the_collection_level_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixture = _write_collection(tmp_path)
    environment_path = tmp_path / "environment.json"
    environment_path.write_text(
        json.dumps(
            {
                "name": "Staging",
                "values": [{"key": "baseUrl", "value": "staging.exemplo.com", "type": "default"}],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "generate",
            "--file",
            str(fixture),
            "-y",
            "--target",
            "playwright",
            "-e",
            str(environment_path),
        ]
    )

    assert exit_code == SUCCESS
    conftest = next((tmp_path / "artifacts").rglob("conftest.py")).read_text(encoding="utf-8")
    # Environment (prioridade 1) nunca é sobrescrito pelo valor de nível de
    # Collection (prioridade 2) para o mesmo nome.
    assert '_DEFAULT_BASE_URL = "https://staging.exemplo.com"' in conftest
    assert "api.exemplo.com" not in conftest
