import json
from collections.abc import Sequence

from api_quality_agent.domain.models import ExecutionContext
from api_quality_agent.shared import (
    HTTP_TRANSACTIONS_PATH_ENV_VAR,
    TRACE_ARTIFACTS_PATH_ENV_VAR,
    TRACE_DIR_ENV_VAR,
)
from api_quality_agent.generators.playwright.assertion_precision import AssertionPrecision
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
from api_quality_agent.generators.playwright.scenario_quality_guard import (
    assert_no_false_positive_smells,
)
from api_quality_agent.generators.playwright.warning_catalog import UNRESOLVED_VARIABLE

_ENDPOINTS_DIR = "endpoints"
_CONFTEST_FILE_NAME = "conftest.py"
_MANIFEST_FILE_NAME = "generation-manifest.json"

# Bumpar deliberadamente (nunca por efeito colateral de outra mudança) toda
# vez que o formato do manifesto ganhar/perder uma chave — mesmo espírito
# de EXECUTION_RESULT_SCHEMA_VERSION (persist_execution_result_use_case.py)
# e do teste de caracterização que trava esse valor
# (tests/characterization/test_execution_result_schema.py). Ver
# tests/unit/test_playwright_manifest_schema.py para o equivalente aqui.
# 1.1 (Parte 23): acrescenta "assertion_classifications".
# 1.2 (Parte 24): warnings "de código" ganham method/location/metadata,
# deduplicados; endpoints ganham "coverage" (complete/partial/not_generated)
# — nunca remove nem renomeia uma chave existente.
_MANIFEST_SCHEMA_VERSION = "1.2"

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

        # Parte 25 — guarda de qualidade "antes da persistência": levanta
        # imediatamente se algum conteúdo já gerado contiver um padrão
        # proibido (falso positivo conhecido) — nunca chega a virar
        # GeneratedFile. Uma violação aqui é sempre um bug do gerador, não
        # algo recuperável silenciando o problema.
        for endpoint_test in endpoint_tests:
            assert_no_false_positive_smells(endpoint_test.content)

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
        "import json\n"
        "import os\n"
        "import uuid\n"
        "from collections.abc import Iterator\n"
        "from typing import Any\n"
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
        f'_HTTP_TRANSACTIONS_PATH_ENV_VAR = "{HTTP_TRANSACTIONS_PATH_ENV_VAR}"\n'
        "# Superfície completa usada pelos testes gerados: get/post/put/patch/\n"
        "# delete/head (nativos) e fetch (usado para OPTIONS). Interceptar\n"
        "# aqui, uma única vez, cobre qualquer endpoint sem depender de nada\n"
        "# específico de um em particular.\n"
        "_HTTP_CAPTURE_METHODS = (\"get\", \"post\", \"put\", \"patch\", \"delete\", \"head\", \"fetch\")\n"
        "\n"
        "# P1.1 (detalhamento de assertions) — correlação test_id -> request ->\n"
        "# response -> assertions: preenchido pela fixture autouse abaixo antes\n"
        "# de cada teste rodar, lido tanto por _record_http_transaction (aqui)\n"
        "# quanto pelo helper _record_assertion_result embutido em cada arquivo\n"
        "# de teste (playwright_endpoint_test_generator.py) — mesmo identificador\n"
        "# nos dois lados, nunca dois esquemas de correlação diferentes.\n"
        "_CURRENT_TEST_ID = \"\"\n"
        "\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _aqo_current_test_id(request: Any) -> Iterator[None]:\n"
        "    global _CURRENT_TEST_ID\n"
        "    _CURRENT_TEST_ID = request.node.name\n"
        "    try:\n"
        "        yield\n"
        "    finally:\n"
        "        _CURRENT_TEST_ID = \"\"\n"
        "\n"
        "\n"
        "def _json_safe(value: Any) -> Any:\n"
        "    # Nunca decide o que é secret (isso é do PlaywrightAdapter, depois) —\n"
        "    # só garante que qualquer coisa passada como data/form/multipart/\n"
        "    # headers vire algo serializável em JSON, sem estourar a captura.\n"
        "    if value is None or isinstance(value, (str, int, float, bool)):\n"
        "        return value\n"
        "    if isinstance(value, bytes):\n"
        "        try:\n"
        "            return value.decode(\"utf-8\")\n"
        "        except UnicodeDecodeError:\n"
        "            return f\"<{len(value)} bytes bin\u00e1rios>\"\n"
        "    if isinstance(value, dict):\n"
        "        return {str(key): _json_safe(item) for key, item in value.items()}\n"
        "    if isinstance(value, (list, tuple)):\n"
        "        return [_json_safe(item) for item in value]\n"
        "    return str(value)\n"
        "\n"
        "\n"
        "def _record_http_transaction(\n"
        "    method: str, response: Any, request_headers: Any, request_body: Any\n"
        ") -> None:\n"
        "    transactions_path = os.environ.get(_HTTP_TRANSACTIONS_PATH_ENV_VAR)\n"
        "    if not transactions_path:\n"
        "        return\n"
        "    try:\n"
        "        response_body = response.text()\n"
        "    except Exception:\n"
        "        response_body = None\n"
        "    try:\n"
        "        response_headers = dict(response.headers)\n"
        "    except Exception:\n"
        "        response_headers = {}\n"
        "    entry = {\n"
        "        \"test_id\": _CURRENT_TEST_ID,\n"
        "        \"method\": method,\n"
        "        \"url\": getattr(response, \"url\", \"\"),\n"
        "        \"request_headers\": _json_safe(request_headers) or {},\n"
        "        \"request_body\": _json_safe(request_body),\n"
        "        \"response_status\": getattr(response, \"status\", 0),\n"
        "        \"response_headers\": response_headers,\n"
        "        \"response_body\": response_body,\n"
        "    }\n"
        "    with open(transactions_path, \"a\", encoding=\"utf-8\") as handle:\n"
        "        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + \"\\n\")\n"
        "\n"
        "\n"
        "def _wrap_for_http_capture(context: APIRequestContext) -> APIRequestContext:\n"
        "    # P1.2 — captura estruturada de transação HTTP: intercepta cada\n"
        "    # método de request, grava o que aconteceu (nunca decide o que é\n"
        "    # secret; mascaramento acontece depois, no PlaywrightAdapter).\n"
        "    for method_name in _HTTP_CAPTURE_METHODS:\n"
        "        original = getattr(context, method_name, None)\n"
        "        if original is None:\n"
        "            continue\n"
        "\n"
        "        def make_wrapper(name: str, call_original: Any) -> Any:\n"
        "            def wrapper(url: str, **kwargs: Any) -> Any:\n"
        "                response = call_original(url, **kwargs)\n"
        "                http_method = kwargs.get(\"method\", \"GET\") if name == \"fetch\" else name.upper()\n"
        "                request_body = kwargs.get(\"data\")\n"
        "                if request_body is None:\n"
        "                    request_body = kwargs.get(\"form\")\n"
        "                if request_body is None:\n"
        "                    request_body = kwargs.get(\"multipart\")\n"
        "                _record_http_transaction(\n"
        "                    http_method, response, kwargs.get(\"headers\"), request_body\n"
        "                )\n"
        "                return response\n"
        "\n"
        "            return wrapper\n"
        "\n"
        "        setattr(context, method_name, make_wrapper(method_name, original))\n"
        "    return context\n"
        "\n"
        "\n"
        f'_TRACE_DIR_ENV_VAR = "{TRACE_DIR_ENV_VAR}"\n'
        f'_TRACE_ARTIFACTS_PATH_ENV_VAR = "{TRACE_ARTIFACTS_PATH_ENV_VAR}"\n'
        "\n"
        "\n"
        "# P1.3 (Trace em falha): hook padrão do pytest (documentado no próprio\n"
        "# pytest — \"Fixture finalization\") para descobrir, DENTRO de uma\n"
        "# fixture, se o teste que está sendo finalizado passou ou falhou. Nunca\n"
        "# decide isso sozinho: só expõe o resultado que o pytest já calculou\n"
        "# (item.rep_call), lido por _test_failed() abaixo.\n"
        "@pytest.hookimpl(tryfirst=True, hookwrapper=True)\n"
        "def pytest_runtest_makereport(item: Any, call: Any) -> Iterator[None]:\n"
        "    outcome = yield\n"
        "    report = outcome.get_result()\n"
        "    setattr(item, \"rep_\" + report.when, report)\n"
        "\n"
        "\n"
        "def _test_failed(request: Any) -> bool:\n"
        "    for phase in (\"setup\", \"call\"):\n"
        "        report = getattr(request.node, \"rep_\" + phase, None)\n"
        "        if report is not None and report.failed:\n"
        "            return True\n"
        "    return False\n"
        "\n"
        "\n"
        "def _start_trace(context: APIRequestContext) -> None:\n"
        "    # Só ativa quando o PlaywrightAdapter pediu (variável definida) —\n"
        "    # ausente = feature desligada, nunca grava nada (suíte antiga\n"
        "    # regenerada, ou pytest rodado fora do adapter). snapshots=True é\n"
        "    # obrigatório para o trace conter QUALQUER atividade de rede (sem\n"
        "    # ele, trace.network fica vazio); sources=False evita embutir\n"
        "    # código-fonte no trace (não agrega valor aqui, só tamanho).\n"
        "    if not os.environ.get(_TRACE_DIR_ENV_VAR):\n"
        "        return\n"
        "    try:\n"
        "        context.tracing.start(snapshots=True, sources=False)\n"
        "    except Exception:\n"
        "        pass\n"
        "\n"
        "\n"
        "def _finish_trace(context: APIRequestContext, request: Any) -> None:\n"
        "    trace_dir = os.environ.get(_TRACE_DIR_ENV_VAR)\n"
        "    if not trace_dir:\n"
        "        return\n"
        "    if not _test_failed(request):\n"
        "        # PASS -> descarta: tracing.stop() sem path nunca escreve\n"
        "        # arquivo nenhum (regra explícita: nunca reter artefato de um\n"
        "        # teste que passou).\n"
        "        try:\n"
        "            context.tracing.stop()\n"
        "        except Exception:\n"
        "            pass\n"
        "        return\n"
        "    # FAIL -> salva. O nome do arquivo é só um identificador local\n"
        "    # opaco (uuid) — a correlação de verdade com test_id é sempre via\n"
        "    # o manifesto NDJSON abaixo, nunca o nome do arquivo sozinho.\n"
        "    raw_path = os.path.join(trace_dir, uuid.uuid4().hex + \".zip\")\n"
        "    manifest_path = os.environ.get(_TRACE_ARTIFACTS_PATH_ENV_VAR)\n"
        "    try:\n"
        "        context.tracing.stop(path=raw_path)\n"
        "    except Exception as error:\n"
        "        # P1.5 (infrastructure failure das evidências): a falha em\n"
        "        # FINALIZAR o Trace (captura em si, Parte _start_trace,\n"
        "        # cascata pra cá quando nunca chegou a iniciar) precisa ser\n"
        "        # visível pro PlaywrightAdapter — nunca só um `return`\n"
        "        # silencioso como antes. Só o NOME da classe da exceção\n"
        "        # (nunca str(error)) — mensagens de exceção do Playwright\n"
        "        # podem ecoar detalhes da chamada (URL, headers) e este\n"
        "        # código gerado não tem known_secret_values pra mascarar;\n"
        "        # o PlaywrightAdapter monta uma mensagem segura e genérica\n"
        "        # a partir só do nome da classe.\n"
        "        if manifest_path:\n"
        "            entry = {\"test_id\": request.node.name, \"error\": type(error).__name__}\n"
        "            with open(manifest_path, \"a\", encoding=\"utf-8\") as handle:\n"
        "                handle.write(json.dumps(entry, ensure_ascii=False) + \"\\n\")\n"
        "        return\n"
        "    if not manifest_path:\n"
        "        return\n"
        "    entry = {\"test_id\": request.node.name, \"path\": raw_path}\n"
        "    with open(manifest_path, \"a\", encoding=\"utf-8\") as handle:\n"
        "        handle.write(json.dumps(entry, ensure_ascii=False) + \"\\n\")\n"
        "\n"
        "\n"
        "@pytest.fixture\n"
        "def api_context(request: Any) -> Iterator[APIRequestContext]:\n"
        "    base_url = os.environ.get(_BASE_URL_ENV_VAR, _DEFAULT_BASE_URL)\n"
        "    with sync_playwright() as playwright:\n"
        "        request_context = playwright.request.new_context(\n"
        "            base_url=base_url, extra_http_headers=_SHARED_HEADERS\n"
        "        )\n"
        "        _start_trace(request_context)\n"
        "        try:\n"
        "            yield _wrap_for_http_capture(request_context)\n"
        "        finally:\n"
        "            _finish_trace(request_context, request)\n"
        "            # P1.4 (hardening): dispose() nunca pode esconder a\n"
        "            # falha original do teste — se o teste falhou (yield\n"
        "            # relançou) e dispose() TAMBÉM levantar aqui, a\n"
        "            # exceção do finally substituiria a original na saída\n"
        "            # do pytest. Mesma postura best-effort já usada por\n"
        "            # _start_trace/_finish_trace.\n"
        "            try:\n"
        "                request_context.dispose()\n"
        "            except Exception:\n"
        "                pass\n"
    )


def _endpoint_method_and_path(endpoint_source: str) -> tuple[str, str]:
    # endpoint_source é sempre "MÉTODO /path" (ver
    # api_analysis_engine._endpoint_source_label) — mesma partição já usada
    # por endpoint_file_naming.endpoint_source_to_slug.
    method, _, path = endpoint_source.strip().partition(" ")
    return method, path


def _endpoint_coverage(endpoint_test: GeneratedEndpointTest) -> str:
    # Regra 4 da Parte 24: "warning deve permitir diferenciar cenário
    # completo; parcial; não gerado" — reaproveita sinais já existentes,
    # nunca uma heurística nova sobre o texto do conteúdo:
    # - "not_generated": scenario_names vazio, mesmo critério de "rendered"
    #   abaixo (endpoint caiu no PlaceholderEndpointTestGenerator).
    # - "partial": cenário renderizado, mas com pelo menos um warning
    #   registrado (header omitido, correlação não confirmável etc.), uma
    #   variável sem resolução, ou uma classificação BROAD (regra 3 da
    #   Parte 23: BROAD nunca conta como "completo").
    # - "complete": renderizado, sem warnings/variáveis pendentes, todas as
    #   classificações EXACT/DERIVED.
    if not endpoint_test.scenario_names:
        return "not_generated"
    if endpoint_test.warnings or endpoint_test.unresolved_variables:
        return "partial"
    if any(
        classification.precision is AssertionPrecision.BROAD
        for classification in endpoint_test.assertion_classifications
    ):
        return "partial"
    return "complete"


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
                "coverage": _endpoint_coverage(endpoint_test),
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


def _code_warning_entry(warning: PlaywrightGenerationWarning) -> dict[str, object]:
    # Parte 24 — forma padronizada de um warning "de código": code, message,
    # endpoint, method, scenario, location e metadata (regra "padronizar
    # warnings com"). method é derivado automaticamente pelo próprio
    # PlaywrightGenerationWarning (__post_init__) quando não informado
    # explicitamente — nunca recalculado aqui.
    return {
        "code": warning.code,
        "endpoint": warning.endpoint,
        "method": warning.method,
        "scenario": warning.scenario,
        "location": warning.location,
        "message": warning.message,
        "metadata": dict(warning.metadata),
    }


def _entry_dedupe_key(entry: dict[str, object]) -> tuple[object, ...]:
    # Regra 2 da Parte 24: "mesmo problema não deve ser duplicado
    # desnecessariamente" — chave estável e hasháveis por valor (metadata é
    # um dict, normalizado para tupla ordenada) para comparar entradas por
    # CONTEÚDO, não por identidade do objeto.
    def normalize(value: object) -> object:
        if isinstance(value, dict):
            return tuple(sorted(value.items()))
        return value

    return tuple((key, normalize(value)) for key, value in sorted(entry.items()))


def _dedupe_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[object, ...]] = set()
    deduped: list[dict[str, object]] = []
    for entry in entries:
        key = _entry_dedupe_key(entry)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _warning_entries(
    endpoint_tests: Sequence[GeneratedEndpointTest],
    naming_warnings: Sequence[PlaywrightGenerationWarning],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = [_code_warning_entry(warning) for warning in naming_warnings]
    for endpoint_test in endpoint_tests:
        for warning in endpoint_test.warnings:
            entries.append(_code_warning_entry(warning))
        for unresolved in endpoint_test.unresolved_variables:
            # Formato do warning obrigatório da Parte 15 (variable/location
            # em vez de message/scenario) — ver exemplo no plano de ação;
            # forma própria, distinta do warning "de código" acima (nunca as
            # duas juntas na mesma entrada).
            entries.append(
                {
                    "code": UNRESOLVED_VARIABLE,
                    "endpoint": endpoint_test.endpoint_source,
                    "variable": unresolved.name,
                    "location": unresolved.location,
                }
            )
    return _dedupe_entries(entries)


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
