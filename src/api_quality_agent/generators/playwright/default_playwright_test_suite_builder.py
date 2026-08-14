import json
from collections.abc import Sequence

from api_quality_agent.domain.models import ExecutionContext
from api_quality_agent.generators.playwright.endpoint_file_naming import (
    ResolvedEndpointFileNames,
    resolve_endpoint_file_names,
)
from api_quality_agent.generators.playwright.generated_endpoint_test import GeneratedEndpointTest
from api_quality_agent.generators.playwright.generated_file import GeneratedFile
from api_quality_agent.generators.playwright.generated_test_suite import GeneratedTestSuite
from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)
from api_quality_agent.generators.playwright.variable_resolver import UNRESOLVED_VARIABLE

_ENDPOINTS_DIR = "endpoints"
_CONFTEST_FILE_NAME = "conftest.py"
_MANIFEST_FILE_NAME = "generation-manifest.json"

# Bumpar deliberadamente (nunca por efeito colateral de outra mudança) toda
# vez que o formato do manifesto ganhar/perder uma chave — mesmo espírito
# de EXECUTION_RESULT_SCHEMA_VERSION (persist_execution_result_use_case.py)
# e do teste de caracterização que trava esse valor
# (tests/characterization/test_execution_result_schema.py). Ver
# tests/unit/test_playwright_manifest_schema.py para o equivalente aqui.
# 1.1 (Parte 23): acrescenta "assertion_classifications" — nunca remove
# nem renomeia uma chave existente.
_MANIFEST_SCHEMA_VERSION = "1.1"

# Pode ser sobrescrito em tempo de execução sem regenerar a suíte (ex.:
# apontar para staging/produção em CI) — nunca uma credencial, só a URL
# base. Nome escolhido para não colidir com variáveis de ambiente comuns.
_BASE_URL_ENV_VAR = "PLAYWRIGHT_BASE_URL"


class DefaultPlaywrightTestSuiteBuilder:
    # Implementação concreta do contrato PlaywrightTestSuiteBuilder (Parte
    # 03): monta a estrutura física da suíte — endpoints/, conftest.py e
    # generation-manifest.json (Parte 06) — a partir dos GeneratedEndpointTest
    # já produzidos. Nunca recria estratégias nem cria expectativas novas;
    # só organiza o que já veio pronto do EndpointTestGenerator, resolvendo
    # colisões de nome de arquivo (reaproveita a Parte 05).
    def build(
        self,
        endpoint_tests: Sequence[GeneratedEndpointTest],
        context: ExecutionContext,
    ) -> GeneratedTestSuite:
        naming = resolve_endpoint_file_names(
            [endpoint_test.endpoint_source for endpoint_test in endpoint_tests]
        )

        endpoint_files = tuple(
            GeneratedFile(relative_path=f"{_ENDPOINTS_DIR}/{file_name}", content=endpoint_test.content)
            for endpoint_test, file_name in zip(endpoint_tests, naming.file_names, strict=True)
        )

        conftest_file = GeneratedFile(
            relative_path=_CONFTEST_FILE_NAME, content=_render_conftest(endpoint_tests)
        )
        manifest_file = GeneratedFile(
            relative_path=_MANIFEST_FILE_NAME,
            content=_render_manifest(endpoint_tests, naming, context),
        )

        warnings = naming.warnings + tuple(
            warning for endpoint_test in endpoint_tests for warning in endpoint_test.warnings
        )

        return GeneratedTestSuite(
            files=(conftest_file, *endpoint_files, manifest_file),
            warnings=warnings,
        )


def _resolve_suite_base_url(endpoint_tests: Sequence[GeneratedEndpointTest]) -> str:
    # Primeiro base_url determinável, na ordem dos endpoints — nunca
    # inventado (ver base_url.py). Coleções com hosts diferentes por
    # endpoint não são totalmente cobertas por um único api_context; isso
    # é uma limitação conhecida, não escondida (o valor pode sempre ser
    # sobrescrito via PLAYWRIGHT_BASE_URL sem regenerar a suíte).
    for endpoint_test in endpoint_tests:
        if endpoint_test.base_url:
            return endpoint_test.base_url
    return ""


def _render_conftest(endpoint_tests: Sequence[GeneratedEndpointTest]) -> str:
    default_base_url = _resolve_suite_base_url(endpoint_tests)

    return (
        '"""conftest.py gerado automaticamente — fixture compartilhada '
        "para os testes de API desta suíte (Playwright APIRequestContext, "
        'sem browser/page)."""\n'
        "\n"
        "import os\n"
        "from collections.abc import Iterator\n"
        "\n"
        "import pytest\n"
        "from playwright.sync_api import APIRequestContext, sync_playwright\n"
        "\n"
        f'_BASE_URL_ENV_VAR = "{_BASE_URL_ENV_VAR}"\n'
        f"_DEFAULT_BASE_URL = {json.dumps(default_base_url)}\n"
        "\n"
        "# Cabeçalhos compartilhados por toda a suíte (Parte 11) — aplicados a\n"
        "# toda requisição feita através deste api_context. Um header definido\n"
        "# diretamente num teste (headers={...} em api_context.get(...)) tem\n"
        "# precedência sobre o que estiver aqui quando o nome coincide —\n"
        "# comportamento nativo do Playwright (headers por requisição\n"
        "# sobrescrevem extra_http_headers do contexto), não algo mesclado\n"
        "# manualmente por este código. Vazio por padrão: nenhum header é\n"
        "# assumido sem evidência real da Collection.\n"
        "_SHARED_HEADERS: dict[str, str] = {}\n"
        "\n"
        "\n"
        "@pytest.fixture\n"
        "def api_context() -> Iterator[APIRequestContext]:\n"
        "    base_url = os.environ.get(_BASE_URL_ENV_VAR, _DEFAULT_BASE_URL)\n"
        "    with sync_playwright() as playwright:\n"
        "        request_context = playwright.request.new_context(\n"
        "            base_url=base_url, extra_http_headers=_SHARED_HEADERS\n"
        "        )\n"
        "        try:\n"
        "            yield request_context\n"
        "        finally:\n"
        "            request_context.dispose()\n"
    )


def _endpoint_method_and_path(endpoint_source: str) -> tuple[str, str]:
    # endpoint_source é sempre "MÉTODO /path" (ver
    # api_analysis_engine._endpoint_source_label) — mesma partição já usada
    # por endpoint_file_naming.endpoint_source_to_slug.
    method, _, path = endpoint_source.strip().partition(" ")
    return method, path


def _endpoint_entries(
    endpoint_tests: Sequence[GeneratedEndpointTest], naming: ResolvedEndpointFileNames
) -> list[dict[str, object]]:
    # "O manifesto lista todos os arquivos realmente existentes": o nome de
    # arquivo aqui é sempre o já resolvido por resolve_endpoint_file_names
    # (com sufixo de colisão quando aplicável), nunca
    # endpoint_test.suggested_file_name cru — o mesmo caminho que de fato
    # vira um GeneratedFile em build().
    entries = []
    for endpoint_test, file_name in zip(endpoint_tests, naming.file_names, strict=True):
        method, path = _endpoint_method_and_path(endpoint_test.endpoint_source)
        entries.append(
            {
                "endpoint": endpoint_test.endpoint_source,
                "method": method,
                "path": path,
                "file": f"{_ENDPOINTS_DIR}/{file_name}",
                # scenario_names só é vazio para o PlaceholderEndpointTestGenerator
                # (fallback) — mesmo sinal já usado internamente pelo gerador,
                # nunca uma heurística nova sobre o texto do conteúdo.
                "rendered": bool(endpoint_test.scenario_names),
            }
        )
    return entries


def _required_environment_variables(endpoint_tests: Sequence[GeneratedEndpointTest]) -> list[str]:
    variables: set[str] = set()
    for endpoint_test in endpoint_tests:
        variables.update(endpoint_test.required_environment_variables)
    return sorted(variables)


def _resolved_variables(endpoint_tests: Sequence[GeneratedEndpointTest]) -> dict[str, str]:
    # Nunca um secret (ver VariableResolutionSession.resolved_variables) —
    # mesclado na ordem dos endpoints; um mesmo nome resolvido para valores
    # diferentes em endpoints diferentes (raro — só possível via default de
    # Collection por path variable) mantém o último, mesmo critério já
    # usado para headers duplicados.
    merged: dict[str, str] = {}
    for endpoint_test in endpoint_tests:
        merged.update(endpoint_test.resolved_variables)
    return merged


def _warning_entries(
    endpoint_tests: Sequence[GeneratedEndpointTest],
    naming_warnings: Sequence[PlaywrightGenerationWarning],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = [
        {
            "code": warning.code,
            "endpoint": warning.endpoint,
            "scenario": warning.scenario,
            "message": warning.message,
        }
        for warning in naming_warnings
    ]
    for endpoint_test in endpoint_tests:
        for warning in endpoint_test.warnings:
            entries.append(
                {
                    "code": warning.code,
                    "endpoint": warning.endpoint,
                    "scenario": warning.scenario,
                    "message": warning.message,
                }
            )
        for unresolved in endpoint_test.unresolved_variables:
            # Formato do warning obrigatório da Parte 15 (variable/location
            # em vez de message/scenario) — ver exemplo no plano de ação.
            entries.append(
                {
                    "code": UNRESOLVED_VARIABLE,
                    "endpoint": endpoint_test.endpoint_source,
                    "variable": unresolved.name,
                    "location": unresolved.location,
                }
            )
    return entries


def _assertion_classifications_section(
    endpoint_tests: Sequence[GeneratedEndpointTest],
) -> dict[str, object]:
    # Parte 23: "a classificação deve chegar... ao generation-manifest.json"
    # — uma entrada por expectativa REALMENTE gerada (nunca uma para uma
    # categoria sem evidência nenhuma) mais um resumo agregado por
    # precisão. "BROAD não pode ser contabilizado como equivalente a EXACT"
    # (regra 3): summary sempre mantém as três chaves separadas, nunca
    # mescladas num único "passed"/"total".
    summary = {"exact": 0, "derived": 0, "broad": 0}
    entries: list[dict[str, object]] = []
    for endpoint_test in endpoint_tests:
        for classification in endpoint_test.assertion_classifications:
            summary[classification.precision.value] += 1
            entries.append(
                {
                    "endpoint": endpoint_test.endpoint_source,
                    "assertion": classification.assertion,
                    "precision": classification.precision.value,
                    "source": classification.source,
                    "justification": classification.justification,
                }
            )
    return {"summary": summary, "entries": entries}


def _render_manifest(
    endpoint_tests: Sequence[GeneratedEndpointTest],
    naming: ResolvedEndpointFileNames,
    context: ExecutionContext,
) -> str:
    # Parte 15: amplia o manifesto mínimo da Parte 06 com rastreabilidade
    # de variáveis (resolvidas sem expor secret, externas necessárias, não
    # resolvidas com localização) e a lista real de endpoints/arquivos
    # gerados — nunca um resumo aproximado, sempre derivado dos mesmos
    # GeneratedEndpointTest que viram os arquivos físicos em build().
    not_rendered = [
        endpoint_test.endpoint_source
        for endpoint_test in endpoint_tests
        if not endpoint_test.scenario_names
    ]

    payload = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "target": "playwright",
        "collection_name": context.collection_name,
        "execution_id": context.execution_id,
        "generated_at": context.started_at.isoformat(),
        "endpoints_analyzed": len(endpoint_tests),
        "endpoint_files_generated": len(endpoint_tests),
        "endpoints": _endpoint_entries(endpoint_tests, naming),
        "endpoints_not_rendered": not_rendered,
        "required_environment_variables": _required_environment_variables(endpoint_tests),
        "resolved_variables": _resolved_variables(endpoint_tests),
        "warnings": _warning_entries(endpoint_tests, naming.warnings),
        "assertion_classifications": _assertion_classifications_section(endpoint_tests),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
