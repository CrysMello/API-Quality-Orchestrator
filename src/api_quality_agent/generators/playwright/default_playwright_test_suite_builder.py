import json
from collections.abc import Sequence

from api_quality_agent.domain.models import ExecutionContext
from api_quality_agent.generators.playwright.endpoint_file_naming import (
    resolve_endpoint_file_names,
)
from api_quality_agent.generators.playwright.generated_endpoint_test import GeneratedEndpointTest
from api_quality_agent.generators.playwright.generated_file import GeneratedFile
from api_quality_agent.generators.playwright.generated_test_suite import GeneratedTestSuite

_ENDPOINTS_DIR = "endpoints"
_CONFTEST_FILE_NAME = "conftest.py"
_MANIFEST_FILE_NAME = "generation-manifest.json"

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
            content=_render_manifest(endpoint_tests, context),
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


def _render_manifest(
    endpoint_tests: Sequence[GeneratedEndpointTest], context: ExecutionContext
) -> str:
    # Manifesto mínimo (Parte 06, deliberadamente incompleto): só o
    # suficiente para confirmar que a suíte foi gerada e para quantas
    # endpoints. Rastreabilidade completa (cenários, warnings por cenário,
    # precisão de asserções, variáveis não resolvidas — ver seção 7 do
    # plano de ação) é escopo de uma etapa futura.
    payload = {
        "target": "playwright",
        "collection_name": context.collection_name,
        "execution_id": context.execution_id,
        "generated_at": context.started_at.isoformat(),
        "endpoints_analyzed": len(endpoint_tests),
        "endpoint_files_generated": len(endpoint_tests),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
