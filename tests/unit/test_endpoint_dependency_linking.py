"""Dependências entre endpoints Playwright — endpoint_dependency_linking.py.

Testa a etapa de LINKAGEM isoladamente (pura, sem geração de código nem
execução) — quem produz o quê, ordem topológica resultante, detecção de
ciclo e a regra de isolamento entre produtores diferentes que por acaso
usam o mesmo nome de variável. Cobre Etapas 3, 7, 8 e 9 do enunciado no
nível estrutural; a geração de código real (Etapas 5/6) tem seu próprio
arquivo (test_playwright_variable_dependency_generation.py) e a execução
real ponta a ponta (Testes 1/4/5/6/7/9 do enunciado) vive em
tests/characterization/test_playwright_variable_dependency_e2e.py.

NormalizedRequest é sempre construído pelo pipeline REAL (PostmanCollectionParser
+ ApiAnalysisEngine) — nunca um dataclass montado à mão, que não provaria
nada sobre a forma real de NormalizedUrl.path/variables.
"""

import json

from api_quality_agent.domain.models import VariableExtraction, VariableScope
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright.endpoint_dependency_linking import (
    EndpointDependencyInput,
    link_endpoint_dependencies,
    producer_test_id_for,
)
from api_quality_agent.generators.playwright.warning_catalog import CIRCULAR_VARIABLE_DEPENDENCY
from api_quality_agent.parsers import PostmanCollectionParser


def _normalized_request(method: str, path_segments: tuple[str, ...], url_variables: list | None = None):
    url: dict = {
        "raw": "https://api.exemplo.com/" + "/".join(path_segments),
        "protocol": "https",
        "host": ["api", "exemplo", "com"],
        "path": list(path_segments),
    }
    if url_variables is not None:
        url["variable"] = url_variables
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "C",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "R", "id": "r", "request": {"method": method, "url": url}}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.normalized_request


def _extraction(variable_name: str, *, json_path: str | None = None) -> VariableExtraction:
    return VariableExtraction(
        variable_name=variable_name,
        source="response.body",
        json_path=json_path or f"$.{variable_name}",
        scope=VariableScope.COLLECTION,
        origin="contract",
    )


def _endpoint(
    endpoint_source: str,
    path_segments: tuple[str, ...],
    *,
    extractions: tuple[VariableExtraction, ...] = (),
    url_variables: list | None = None,
) -> EndpointDependencyInput:
    method = endpoint_source.split(" ", 1)[0]
    return EndpointDependencyInput(
        endpoint_source=endpoint_source,
        normalized_request=_normalized_request(method, path_segments, url_variables),
        variable_extractions=extractions,
    )


# === Etapa 3 — matching producer -> consumer =================================


def test_consumer_matches_producer_and_receives_a_variable_usage():
    producer = _endpoint(
        "POST /customers", ("customers",), extractions=(_extraction("customer_id"),)
    )
    consumer = _endpoint("GET /customers/{customer_id}", ("customers", "{customer_id}"))

    result = link_endpoint_dependencies([producer, consumer])

    consumer_link = result.linked_endpoints[1]
    assert len(consumer_link.variable_usages) == 1
    usage = consumer_link.variable_usages[0]
    assert usage.variable_name == "customer_id"
    assert usage.producer_test_id == producer_test_id_for("POST /customers")
    assert usage.location == "path"

    producer_link = result.linked_endpoints[0]
    assert producer_link.claimed_extraction_names == {"customer_id"}
    assert not result.warnings


def test_a_collection_default_never_becomes_a_runtime_dependency():
    # url.variable[] já declara um valor literal para "customer_id" — isso
    # continua resolvido por VariableResolutionSession normalmente, nunca
    # uma dependência de runtime, mesmo com um produtor candidato presente.
    producer = _endpoint(
        "POST /customers", ("customers",), extractions=(_extraction("customer_id"),)
    )
    consumer = _endpoint(
        "GET /customers/{customer_id}",
        ("customers", "{customer_id}"),
        url_variables=[{"key": "customer_id", "value": "42"}],
    )

    result = link_endpoint_dependencies([producer, consumer])

    assert result.linked_endpoints[1].variable_usages == ()


# === Etapa 7 — ordenação a partir das dependências ===========================


def test_independent_endpoint_keeps_its_relative_order_and_gets_no_dependency():
    producer = _endpoint(
        "POST /customers", ("customers",), extractions=(_extraction("customer_id"),)
    )
    independent = _endpoint("GET /products", ("products",))
    consumer = _endpoint("GET /customers/{customer_id}", ("customers", "{customer_id}"))

    result = link_endpoint_dependencies([producer, independent, consumer])

    assert result.order == (0, 1, 2)
    assert result.linked_endpoints[1].variable_usages == ()
    assert result.linked_endpoints[1].claimed_extraction_names == set()


def test_order_places_the_producer_before_the_consumer_even_when_declared_after():
    consumer = _endpoint("GET /customers/{customer_id}", ("customers", "{customer_id}"))
    producer = _endpoint(
        "POST /customers", ("customers",), extractions=(_extraction("customer_id"),)
    )

    result = link_endpoint_dependencies([consumer, producer])

    producer_index, consumer_index = 1, 0
    assert result.order.index(producer_index) < result.order.index(consumer_index)
    assert result.linked_endpoints[0].variable_usages[0].producer_test_id == (
        producer_test_id_for("POST /customers")
    )


def test_three_step_chain_is_ordered_producer_then_each_consumer_in_turn():
    post_customers = _endpoint(
        "POST /customers", ("customers",), extractions=(_extraction("customer_id"),)
    )
    get_customer = _endpoint("GET /customers/{customer_id}", ("customers", "{customer_id}"))
    update_customer = _endpoint("PUT /customers/{customer_id}", ("customers", "{customer_id}"))

    result = link_endpoint_dependencies([post_customers, get_customer, update_customer])

    assert result.order == (0, 1, 2)
    assert result.linked_endpoints[1].variable_usages[0].producer_test_id == (
        producer_test_id_for("POST /customers")
    )
    assert result.linked_endpoints[2].variable_usages[0].producer_test_id == (
        producer_test_id_for("POST /customers")
    )
    assert result.linked_endpoints[0].claimed_extraction_names == {"customer_id"}


# === Etapa 8 — ciclos =========================================================


def test_a_direct_cycle_is_detected_and_neither_side_is_linked():
    # A produz "b_id" mas seu próprio path depende de "{a_id}"; B produz
    # "a_id" mas seu path depende de "{b_id}" — A -> B -> A.
    endpoint_a = _endpoint(
        "POST /a/{a_id}", ("a", "{a_id}"), extractions=(_extraction("b_id"),)
    )
    endpoint_b = _endpoint(
        "POST /b/{b_id}", ("b", "{b_id}"), extractions=(_extraction("a_id"),)
    )

    result = link_endpoint_dependencies([endpoint_a, endpoint_b])

    assert result.linked_endpoints[0].variable_usages == ()
    assert result.linked_endpoints[1].variable_usages == ()
    assert result.linked_endpoints[0].claimed_extraction_names == set()
    assert result.linked_endpoints[1].claimed_extraction_names == set()
    assert set(result.order) == {0, 1}  # nenhum índice descartado
    # Uma entrada por aresta do ciclo removida (2 arestas neste ciclo
    # direto A<->B) — nunca uma única entrada genérica escondendo qual
    # variável/endpoint especificamente formava o laço.
    assert len(result.warnings) == 2
    assert all(warning.code == CIRCULAR_VARIABLE_DEPENDENCY for warning in result.warnings)


def test_a_longer_cycle_through_three_endpoints_is_also_detected():
    # A -> B -> C -> A.
    endpoint_a = _endpoint(
        "POST /a/{c_id}", ("a", "{c_id}"), extractions=(_extraction("a_id"),)
    )
    endpoint_b = _endpoint(
        "POST /b/{a_id}", ("b", "{a_id}"), extractions=(_extraction("b_id"),)
    )
    endpoint_c = _endpoint(
        "POST /c/{b_id}", ("c", "{b_id}"), extractions=(_extraction("c_id"),)
    )

    result = link_endpoint_dependencies([endpoint_a, endpoint_b, endpoint_c])

    assert all(
        linked.variable_usages == () and linked.claimed_extraction_names == set()
        for linked in result.linked_endpoints
    )
    assert result.warnings
    assert all(warning.code == CIRCULAR_VARIABLE_DEPENDENCY for warning in result.warnings)


# === Etapa 9 — isolamento estrutural (producer -> consumer, não só nome) ====


def test_two_independent_chains_sharing_a_variable_name_never_cross_link():
    # Duas cadeias completamente independentes que por acaso usam o MESMO
    # nome de variável ("resource_id") — o consumidor de cada cadeia
    # precisa linkar ao SEU PRÓPRIO produtor, nunca ao produtor da outra
    # cadeia só porque o nome bate.
    producer_1 = _endpoint(
        "POST /alpha", ("alpha",), extractions=(_extraction("resource_id"),)
    )
    consumer_1 = _endpoint("GET /alpha/{resource_id}", ("alpha", "{resource_id}"))
    producer_2 = _endpoint(
        "POST /beta", ("beta",), extractions=(_extraction("resource_id"),)
    )
    consumer_2 = _endpoint("GET /beta/{resource_id}", ("beta", "{resource_id}"))

    result = link_endpoint_dependencies([producer_1, consumer_1, producer_2, consumer_2])

    usage_1 = result.linked_endpoints[1].variable_usages[0]
    assert usage_1.producer_test_id == producer_test_id_for("POST /alpha")

    usage_2 = result.linked_endpoints[3].variable_usages[0]
    assert usage_2.producer_test_id == producer_test_id_for("POST /beta")

    assert result.linked_endpoints[0].claimed_extraction_names == {"resource_id"}
    assert result.linked_endpoints[2].claimed_extraction_names == {"resource_id"}


def test_producer_test_id_is_the_same_formula_generator_uses_for_the_function_name():
    from api_quality_agent.generators.playwright.endpoint_file_naming import (
        endpoint_source_to_slug,
    )

    endpoint_source = "POST /customers"
    assert producer_test_id_for(endpoint_source) == (
        f"test_{endpoint_source_to_slug(endpoint_source)}_success"
    )


# === P3.3 — matching é sempre por variable_name, nunca por json_path =========
# O linker nunca vê json_path (só variable_name, via
# EndpointDependencyInput.variable_extractions) — estes testes provam,
# explicitamente, que json_path pode divergir livremente do variable_name
# sem afetar (nem habilitar por acidente) o matching.


def test_caso_a_producer_json_path_id_variable_name_customer_id_links():
    # json_path="id" (onde buscar o valor) e variable_name="customer_id"
    # (nome de negócio) coexistem — o consumidor casa pelo variable_name.
    producer = _endpoint(
        "POST /customers",
        ("customers",),
        extractions=(_extraction("customer_id", json_path="$.id"),),
    )
    consumer = _endpoint("GET /customers/{customer_id}", ("customers", "{customer_id}"))

    result = link_endpoint_dependencies([producer, consumer])

    assert len(result.linked_endpoints[1].variable_usages) == 1
    usage = result.linked_endpoints[1].variable_usages[0]
    assert usage.variable_name == "customer_id"
    assert usage.producer_test_id == producer_test_id_for("POST /customers")


def test_caso_b_producer_variable_name_id_never_matches_consumer_customer_id():
    # Mesmo json_path ("id"), mas SEM um nome semântico explícito
    # (variable_name também "id") — nunca "parecido o suficiente" com
    # "customer_id"; nenhum matching heurístico é aplicado.
    producer = _endpoint(
        "POST /customers", ("customers",), extractions=(_extraction("id", json_path="$.id"),)
    )
    consumer = _endpoint("GET /customers/{customer_id}", ("customers", "{customer_id}"))

    result = link_endpoint_dependencies([producer, consumer])

    assert result.linked_endpoints[1].variable_usages == ()
    assert result.linked_endpoints[0].claimed_extraction_names == set()


def test_caso_c_customer_id_order_id_user_id_stay_isolated():
    # Três cadeias independentes, três nomes semânticos distintos, mesmo
    # json_path ("id") nas três — nenhuma se confunde com a outra.
    customers_producer = _endpoint(
        "POST /customers",
        ("customers",),
        extractions=(_extraction("customer_id", json_path="$.id"),),
    )
    customers_consumer = _endpoint("GET /customers/{customer_id}", ("customers", "{customer_id}"))
    orders_producer = _endpoint(
        "POST /orders", ("orders",), extractions=(_extraction("order_id", json_path="$.id"),)
    )
    orders_consumer = _endpoint("GET /orders/{order_id}", ("orders", "{order_id}"))
    users_producer = _endpoint(
        "POST /users", ("users",), extractions=(_extraction("user_id", json_path="$.id"),)
    )
    users_consumer = _endpoint("GET /users/{user_id}", ("users", "{user_id}"))

    result = link_endpoint_dependencies(
        [
            customers_producer,
            customers_consumer,
            orders_producer,
            orders_consumer,
            users_producer,
            users_consumer,
        ]
    )

    assert result.linked_endpoints[1].variable_usages[0].producer_test_id == (
        producer_test_id_for("POST /customers")
    )
    assert result.linked_endpoints[3].variable_usages[0].producer_test_id == (
        producer_test_id_for("POST /orders")
    )
    assert result.linked_endpoints[5].variable_usages[0].producer_test_id == (
        producer_test_id_for("POST /users")
    )


def test_caso_d_two_producers_same_variable_name_no_cross_chain_leak():
    # Dois produtores diferentes produzindo o MESMO nome de variável
    # ("customer_id", ex.: um script de teste nomeando os dois assim) —
    # cada consumidor liga ao produtor mais próximo, nunca ao outro.
    producer_1 = _endpoint(
        "POST /customers",
        ("customers",),
        extractions=(_extraction("customer_id", json_path="$.id"),),
    )
    consumer_1 = _endpoint("GET /customers/{customer_id}", ("customers", "{customer_id}"))
    producer_2 = _endpoint(
        "POST /legacy-customers",
        ("legacy-customers",),
        extractions=(_extraction("customer_id", json_path="$.id"),),
    )
    consumer_2 = _endpoint(
        "GET /legacy-customers/{customer_id}", ("legacy-customers", "{customer_id}")
    )

    result = link_endpoint_dependencies([producer_1, consumer_1, producer_2, consumer_2])

    assert result.linked_endpoints[1].variable_usages[0].producer_test_id == (
        producer_test_id_for("POST /customers")
    )
    assert result.linked_endpoints[3].variable_usages[0].producer_test_id == (
        producer_test_id_for("POST /legacy-customers")
    )
