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

_CONFTEST_PLACEHOLDER_CONTENT = (
    '"""conftest.py gerado — fixtures reais (ex.: api_request_context, '
    "resolução de variáveis/segredos) ainda não implementadas (ver plano "
    'de ação Playwright)."""\n'
)


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
            relative_path=_CONFTEST_FILE_NAME, content=_CONFTEST_PLACEHOLDER_CONTENT
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
