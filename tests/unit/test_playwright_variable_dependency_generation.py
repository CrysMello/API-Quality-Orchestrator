"""Dependências entre endpoints Playwright — geração de código real
(Etapas 5 e 6 do enunciado), no nível do PlaywrightEndpointTestGenerator.

Exercita o gerador REAL diretamente com uma TestStrategy já "linkada" à
mão (variable_extractions/variable_usages) — a etapa de linkagem em si
(quem produz o quê, ordem, ciclos, isolamento) tem seu próprio arquivo,
test_endpoint_dependency_linking.py. A execução real ponta a ponta
(produtor -> runtime state -> consumidor rodando de verdade) vive em
tests/characterization/test_playwright_variable_dependency_e2e.py — este
arquivo só prova que o CÓDIGO GERADO tem a forma certa, nunca que ele
funciona em runtime (isso é o papel do E2E).
"""

import ast
import json

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    TestStrategy,
    VariableExtraction,
    VariableScope,
    VariableUsage,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import PlaywrightEndpointTestGenerator
from api_quality_agent.generators.playwright.endpoint_dependency_linking import (
    producer_test_id_for,
)
from api_quality_agent.generators.playwright.playwright_endpoint_test_generator import (
    _render_helpers_block,
)
from api_quality_agent.parsers import PostmanCollectionParser

_STATUS_201 = (
    AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description="Status code da resposta deve ser 201.",
        expected_value=201,
        origin="contract",
    ),
)
_STATUS_200 = (
    AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description="Status code da resposta deve ser 200.",
        expected_value=200,
        origin="contract",
    ),
)

_PRODUCER_ENDPOINT_SOURCE = "POST /customers"
_PRODUCER_TEST_ID = producer_test_id_for(_PRODUCER_ENDPOINT_SOURCE)


def _analyzed(request: dict):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Collection",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": [{"name": "R1", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


_PRODUCER_REQUEST = {"request": {"method": "POST", "url": "https://api.exemplo.com/customers"}}


def _consumer_request(method: str) -> dict:
    return {
        "request": {
            "method": method,
            "url": {
                "raw": "https://api.exemplo.com/customers/{customer_id}",
                "protocol": "https",
                "host": ["api", "exemplo", "com"],
                "path": ["customers", "{customer_id}"],
                "variable": [],
            },
        }
    }


def _customer_id_extraction() -> VariableExtraction:
    return VariableExtraction(
        variable_name="customer_id",
        source="response.body",
        json_path="$.id",
        scope=VariableScope.COLLECTION,
        origin="contract",
    )


def _customer_id_usage() -> VariableUsage:
    return VariableUsage(
        variable_name="customer_id", producer_test_id=_PRODUCER_TEST_ID, location="path"
    )


# === Etapa 5 — extração no teste produtor ====================================


def test_producer_generates_functional_extraction_and_storage_code():
    _, normalized_request = _analyzed(_PRODUCER_REQUEST)
    strategy = TestStrategy(
        endpoint_source=_PRODUCER_ENDPOINT_SOURCE,
        assertions=_STATUS_201,
        variable_extractions=(_customer_id_extraction(),),
        negative_cases=(),
        warnings=(),
    )

    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)

    ast.parse(generated.content)  # sempre um .py válido, nunca um esqueleto quebrado
    assert "@pytest.mark.skip" not in generated.content  # nunca cai no fallback

    # Extrai o valor via json_path ("$.id" -> ("id",)) usando o helper já
    # existente no projeto (_get_nested_value), nunca uma segunda forma de
    # navegar o JSON.
    assert 'customer_id = _get_nested_value(body, ("id",))' in generated.content
    # Valida que o valor foi realmente encontrado antes de armazenar —
    # nunca armazena None silenciosamente.
    assert "if customer_id is None:" in generated.content
    assert "_assertion_failures.append(" in generated.content
    # Armazena no runtime state associado ao PRÓPRIO test_id (o produtor).
    assert (
        f'_set_shared_variable("{_PRODUCER_TEST_ID}", "customer_id", customer_id)'
        in generated.content
    )
    assert "def _set_shared_variable(producer_test_id, variable_name, value):" in generated.content
    assert "PLAYWRIGHT_SHARED_VARIABLES_PATH" in generated.content


def test_producer_without_any_claimed_extraction_never_emits_shared_variable_code():
    # strategy.variable_extractions vazio (nenhum outro endpoint reivindica
    # nada deste) — regressão: nenhum código de dependência é emitido à
    # toa para um endpoint comum.
    _, normalized_request = _analyzed(_PRODUCER_REQUEST)
    strategy = TestStrategy(
        endpoint_source=_PRODUCER_ENDPOINT_SOURCE,
        assertions=_STATUS_201,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)

    assert "_set_shared_variable" not in generated.content
    assert "_get_shared_variable" not in generated.content
    assert "customer_id" not in generated.content


# === Etapa 6 — consumo no teste dependente ===================================


def test_consumer_never_emits_the_raw_placeholder_or_an_empty_path():
    _, normalized_request = _analyzed(_consumer_request("GET"))
    strategy = TestStrategy(
        endpoint_source="GET /customers/{customer_id}",
        assertions=_STATUS_200,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
        variable_usages=(_customer_id_usage(),),
    )

    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)

    ast.parse(generated.content)
    assert "@pytest.mark.skip" not in generated.content  # nunca cai no fallback
    assert generated.unresolved_variables == ()  # resolvido em runtime, nunca "não resolvido"

    # Nunca o placeholder cru, nem uma URL com valor vazio.
    assert 'api_context.get("/customers/{customer_id}")' not in generated.content
    assert 'api_context.get("/customers/")' not in generated.content
    assert 'api_context.get("/customers")' not in generated.content

    # Recupera o valor produzido em runtime pelo PAR (producer_test_id,
    # variable_name) — nunca só pelo nome da variável.
    assert (
        f'customer_id = _get_shared_variable("{_PRODUCER_TEST_ID}", "customer_id")'
        in generated.content
    )
    # Falha explícita, nunca um valor vazio/inventado quando indisponível.
    assert "assert customer_id is not None," in generated.content
    assert "indisponível" in generated.content
    assert f"Dependência: {_PRODUCER_TEST_ID}." in generated.content
    assert "não ter sido executado" in generated.content

    # A chamada real usa o valor recuperado, via f-string.
    assert 'response = api_context.get(f"/customers/{customer_id}")' in generated.content


def test_consumer_without_a_variable_usage_falls_back_exactly_as_before():
    # Regressão: sem VariableUsage (variable_usages=() default), o
    # comportamento é EXATAMENTE o mesmo de antes desta parte — cai no
    # fallback (placeholder), nunca uma tentativa de dependência.
    _, normalized_request = _analyzed(_consumer_request("GET"))
    strategy = TestStrategy(
        endpoint_source="GET /customers/{customer_id}",
        assertions=_STATUS_200,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)

    assert "@pytest.mark.skip" in generated.content
    assert len(generated.unresolved_variables) == 1
    assert generated.unresolved_variables[0].name == "customer_id"
    assert "_get_shared_variable" not in generated.content


def test_a_fully_literal_path_renders_byte_identical_to_before_this_part():
    # Nenhum segmento dinâmico: _relative_path_from_segments deve produzir
    # exatamente o mesmo texto de sempre (_python_string_literal do path
    # inteiro) — nenhuma regressão para o caso comum (sem dependências).
    _, normalized_request = _analyzed({"request": {"method": "GET", "url": "https://api.exemplo.com/customers"}})
    strategy = TestStrategy(
        endpoint_source="GET /customers",
        assertions=_STATUS_200,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)

    assert 'response = api_context.get("/customers")' in generated.content
    assert "f\"" not in generated.content  # nenhuma f-string de path envolvida


# === Etapa 7/9 — múltiplos consumidores do mesmo produtor ====================


def test_multiple_consumers_can_read_the_same_producer_value():
    usage = _customer_id_usage()
    get_strategy = TestStrategy(
        endpoint_source="GET /customers/{customer_id}",
        assertions=_STATUS_200,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
        variable_usages=(usage,),
    )
    put_strategy = TestStrategy(
        endpoint_source="PUT /customers/{customer_id}",
        assertions=_STATUS_200,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
        variable_usages=(usage,),
    )

    _, get_request = _analyzed(_consumer_request("GET"))
    _, put_request = _analyzed(_consumer_request("PUT"))

    generated_get = PlaywrightEndpointTestGenerator().generate_endpoint(get_strategy, get_request)
    generated_put = PlaywrightEndpointTestGenerator().generate_endpoint(put_strategy, put_request)

    expected_lookup = f'customer_id = _get_shared_variable("{_PRODUCER_TEST_ID}", "customer_id")'
    assert expected_lookup in generated_get.content
    assert expected_lookup in generated_put.content
    assert 'api_context.get(f"/customers/{customer_id}")' in generated_get.content
    assert 'api_context.put(' in generated_put.content
    assert 'f"/customers/{customer_id}"' in generated_put.content


# === Teste 1 (Etapa 10) — extração, execução real do texto dos helpers =====
# Mesmo padrão já usado em test_playwright_field_type_assertions.py: exec()
# do texto REAL de _HELPER_SOURCES (nunca uma reimplementação/cópia) para
# provar o comportamento em runtime sem precisar de um arquivo .py físico
# nem de pytest/Playwright reais — a cadeia ponta a ponta com HTTP real
# fica em tests/characterization/test_playwright_variable_dependency_e2e.py.


def _exec_helpers(names: set) -> dict:
    source = "import json\nimport os\n\n\n" + _render_helpers_block(frozenset(names))
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102 - texto do próprio gerador, não input externo
    return namespace


def test_post_customers_response_id_123_produces_customer_id_123(tmp_path, monkeypatch):
    shared_path = tmp_path / "shared-variables.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_SHARED_VARIABLES_PATH", str(shared_path))
    namespace = _exec_helpers({"get_nested_value", "set_shared_variable", "get_shared_variable"})

    body = {"id": 123, "name": "Ada"}
    customer_id = namespace["_get_nested_value"](body, ("id",))
    assert customer_id == 123

    namespace["_set_shared_variable"]("test_post_customers_success", "customer_id", customer_id)

    lines = shared_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "producer_test_id": "test_post_customers_success",
        "variable_name": "customer_id",
        "value": 123,
    }
    assert (
        namespace["_get_shared_variable"]("test_post_customers_success", "customer_id") == 123
    )


# === Teste 6 (Etapa 10) — isolamento na camada de estado runtime ===========
# A chave de correlação é sempre o PAR (producer_test_id, variable_name),
# nunca só o nome: Teste A -> customer_id = 111 nunca vaza para o
# consumidor de B, e vice-versa.


def test_shared_variable_isolation_across_two_producers_with_the_same_variable_name(
    tmp_path, monkeypatch
):
    shared_path = tmp_path / "shared-variables.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_SHARED_VARIABLES_PATH", str(shared_path))
    namespace = _exec_helpers({"set_shared_variable", "get_shared_variable"})

    namespace["_set_shared_variable"]("test_post_a_success", "resource_id", 111)
    namespace["_set_shared_variable"]("test_post_b_success", "resource_id", 222)

    assert namespace["_get_shared_variable"]("test_post_a_success", "resource_id") == 111
    assert namespace["_get_shared_variable"]("test_post_b_success", "resource_id") == 222
    # Um terceiro "produtor" nunca declarado nunca resolve para nenhum
    # valor — nunca inventa, nunca cai para o primeiro que achar.
    assert namespace["_get_shared_variable"]("test_post_c_success", "resource_id") is None


def test_get_shared_variable_returns_none_when_nothing_was_ever_stored(tmp_path, monkeypatch):
    shared_path = tmp_path / "shared-variables.ndjson"
    monkeypatch.setenv("PLAYWRIGHT_SHARED_VARIABLES_PATH", str(shared_path))
    namespace = _exec_helpers({"get_shared_variable"})

    assert namespace["_get_shared_variable"]("test_post_customers_success", "customer_id") is None


def test_set_shared_variable_is_a_no_op_when_the_env_var_is_absent(monkeypatch):
    # Feature desligada (nenhuma env var configurada, ex.: pytest rodado
    # fora do PlaywrightAdapter) — nunca levanta exceção, nunca cria
    # arquivo nenhum.
    monkeypatch.delenv("PLAYWRIGHT_SHARED_VARIABLES_PATH", raising=False)
    namespace = _exec_helpers({"set_shared_variable"})

    namespace["_set_shared_variable"]("test_post_customers_success", "customer_id", 123)
