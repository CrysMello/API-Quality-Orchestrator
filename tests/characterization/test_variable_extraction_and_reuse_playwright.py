"""Teste de caracterização — variáveis e dependências entre requests no
gerador Playwright.

Investiga, com o gerador REAL (PlaywrightEndpointTestGenerator, nunca um
mock), se a cadeia

    TestStrategy -> extração de valor da resposta -> variável
    -> segundo request usa a variável -> Playwright Generator
    -> código Playwright gerado

funciona hoje. Não é um teste de requisito novo — fotografa o
comportamento ATUAL, para que uma mudança futura (implementar ou remover
esse suporte) seja uma decisão consciente, nunca uma regressão silenciosa
(mesmo critério de tests/characterization/README.md).

NENHUM código de produção foi alterado para este teste. Se algum destes
testes falhar no futuro porque a funcionalidade foi implementada, é
esperado — atualize-o conscientemente (ver README.md).
"""

import json

import pytest

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    TestStrategy,
    VariableExtraction,
    VariableScope,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import PlaywrightEndpointTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser

_STATUS_ASSERTIONS = (
    AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description="Status code da resposta deve ser 201.",
        expected_value=201,
        origin="contract",
    ),
)


def _analyzed(request: dict):
    # Mesmo pipeline real usado pelo restante da suíte de geração Playwright
    # (PostmanCollectionParser + ApiAnalysisEngine) — nunca um
    # NormalizedRequest construído à mão, que não provaria nada sobre o
    # comportamento real do parser/analisador.
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Collection",
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                },
                "item": [{"name": "R1", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


_REQUEST_A = {
    "request": {
        "method": "POST",
        "url": "https://api.exemplo.com/customers",
    }
}

# Path param no estilo OpenAPI/Postman de UM par de chaves ({nome}) — o
# mesmo estilo do exemplo conceitual da tarefa (`/customers/{customer_id}`).
# Sem default declarado em url.variable[] (Collection) — exatamente o
# cenário "valor só existiria em runtime, produzido por outro teste".
_REQUEST_B = {
    "request": {
        "method": "GET",
        "url": {
            "raw": "https://api.exemplo.com/customers/{customer_id}",
            "protocol": "https",
            "host": ["api", "exemplo", "com"],
            "path": ["customers", "{customer_id}"],
            "variable": [],
        },
    }
}


# === Teste 1 — TestStrategy possui a informação necessária ==================


def test_variable_extraction_model_represents_name_source_and_json_path():
    # 1/2/3: VariableExtraction (já existente, nenhum campo novo) representa
    # nome da variável, origem ("response.body") e caminho JSON
    # ("$.id") — sem qualquer alteração de modelo.
    strategy_source_a, _ = _analyzed(_REQUEST_A)
    extraction = VariableExtraction(
        variable_name="customer_id",
        source="response.body",
        json_path="$.id",
        scope=VariableScope.COLLECTION,
        origin="contract",
    )
    strategy_a = TestStrategy(
        endpoint_source=strategy_source_a.source,
        assertions=_STATUS_ASSERTIONS,
        variable_extractions=(extraction,),
        negative_cases=(),
        warnings=(),
    )

    assert len(strategy_a.variable_extractions) == 1
    stored = strategy_a.variable_extractions[0]
    assert stored.variable_name == "customer_id"
    assert stored.source == "response.body"
    assert stored.json_path == "$.id"
    assert stored.scope == VariableScope.COLLECTION


def test_test_strategy_has_no_field_declaring_that_a_request_uses_a_variable():
    # 4/5: TestStrategy é por-endpoint (endpoint_source: str, singular) e
    # não tem NENHUM campo do tipo "variable_usages"/"dependencies" que
    # declare "este request usa a variável X produzida pelo endpoint Y".
    # A única forma de uma "reutilização" aparecer é IMPLÍCITA: um token de
    # path parameter cru (`{customer_id}`) dentro da própria URL do
    # NormalizedRequest do segundo endpoint — nunca uma referência
    # estrutural à VariableExtraction de outro endpoint.
    field_names = {field.name for field in TestStrategy.__dataclass_fields__.values()}

    assert field_names == {
        "endpoint_source",
        "assertions",
        "variable_extractions",
        "negative_cases",
        "warnings",
    }
    assert "variable_usages" not in field_names
    assert "dependencies" not in field_names
    assert "depends_on" not in field_names

    # A "reutilização" só existe hoje como texto cru dentro da URL do
    # segundo request — nunca uma referência ao VariableExtraction de A.
    _, normalized_request_b = _analyzed(_REQUEST_B)
    assert "{customer_id}" in normalized_request_b.url.raw


# === Teste 2 — Generator transforma a estratégia em Playwright ==============


def test_generator_never_reads_variable_extractions_from_the_strategy():
    # O gerador real (PlaywrightEndpointTestGenerator) recebe uma
    # TestStrategy com variable_extractions preenchido para o endpoint A
    # (POST /customers) — o código gerado não armazena nem referencia
    # "customer_id" em lugar nenhum: a extração é lida pelo gerador Postman
    # (postman_test_generator.py), nunca pelo Playwright.
    strategy_source_a, normalized_request_a = _analyzed(_REQUEST_A)
    extraction = VariableExtraction(
        variable_name="customer_id",
        source="response.body",
        json_path="$.id",
        scope=VariableScope.COLLECTION,
        origin="contract",
    )
    strategy_a = TestStrategy(
        endpoint_source=strategy_source_a.source,
        assertions=_STATUS_ASSERTIONS,
        variable_extractions=(extraction,),
        negative_cases=(),
        warnings=(),
    )

    generated_a = PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy_a, normalized_request_a
    )

    assert "customer_id" not in generated_a.content
    assert "@pytest.mark.skip" not in generated_a.content  # POST simples é suportado
    assert 'api_context.post("/customers")' in generated_a.content
    assert "assert response.status == 201" in generated_a.content


def test_generator_falls_back_for_the_request_that_would_reuse_the_variable():
    # Endpoint B (GET /customers/{customer_id}) — sem default na Collection
    # para "customer_id" e sem NENHUM mecanismo de "produzido por outro
    # teste" — cai no fallback (mesmo comportamento hoje documentado para
    # qualquer path variable sem default, ver
    # test_playwright_endpoint_test_generator.py::
    # test_path_variable_without_collection_default_falls_back_with_unresolved_variable).
    strategy_source_b, normalized_request_b = _analyzed(_REQUEST_B)
    strategy_b = TestStrategy(
        endpoint_source=strategy_source_b.source,
        assertions=_STATUS_ASSERTIONS,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated_b = PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy_b, normalized_request_b
    )

    assert "@pytest.mark.skip" in generated_b.content
    assert len(generated_b.unresolved_variables) == 1
    assert generated_b.unresolved_variables[0].name == "customer_id"
    assert generated_b.unresolved_variables[0].location == "path"
    assert generated_b.warnings  # gerador registra explicitamente por que caiu no fallback


# === Teste 3 — Não aceitar placeholder não resolvido =========================


def test_fallback_never_emits_an_unresolved_literal_call():
    # O mecanismo real deste gerador para "não sei resolver" NÃO é uma
    # f-string nem um .format() com o placeholder ainda cru — é
    # PlaceholderEndpointTestGenerator, que produz um teste vazio, marcado
    # @pytest.mark.skip, sem NENHUMA chamada real a api_context. Ou seja: o
    # gerador nunca produz `api_context.get("/customers/{customer_id}")`
    # (chamada real com placeholder não resolvido) — ele simplesmente não
    # produz uma chamada real nenhuma para este endpoint.
    strategy_source_b, normalized_request_b = _analyzed(_REQUEST_B)
    strategy_b = TestStrategy(
        endpoint_source=strategy_source_b.source,
        assertions=_STATUS_ASSERTIONS,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated_b = PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy_b, normalized_request_b
    )

    # "{customer_id}" pode aparecer num COMENTÁRIO informativo
    # ("# Endpoint: GET /customers/{customer_id}") — o que nunca pode
    # existir é uma chamada real a api_context com o placeholder cru
    # dentro, executável e fadada a nunca resolver.
    assert "api_context." not in generated_b.content
    assert 'api_context.get("/customers/{customer_id}")' not in generated_b.content
    assert "def test_placeholder() -> None:" in generated_b.content
    assert "..." in generated_b.content
    # O próprio placeholder já documenta, na origem, que extração de
    # variáveis está fora do que ele implementa.
    assert "extração de" in generated_b.content


# === Teste 4 — Execução real, se a infraestrutura existente permitir ========


def test_runtime_chaining_cannot_be_validated_with_current_infrastructure():
    # Pré-condição confirmada pelos testes 2/3: o endpoint que reutilizaria
    # a variável nunca vira uma chamada HTTP real no código gerado — não há
    # o que executar para provar que o valor de A chegaria a B em runtime.
    # Registrar isso explicitamente em vez de simular uma infraestrutura
    # nova (fora do escopo desta tarefa).
    strategy_source_b, normalized_request_b = _analyzed(_REQUEST_B)
    strategy_b = TestStrategy(
        endpoint_source=strategy_source_b.source,
        assertions=_STATUS_ASSERTIONS,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated_b = PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy_b, normalized_request_b
    )
    assert "api_context." not in generated_b.content  # nada a executar

    pytest.skip("Não foi possível validar execução runtime com a infraestrutura atual.")


# === Teste 5 — Isolamento ====================================================


def test_generating_endpoint_a_never_affects_endpoint_b_generation():
    # Não existe hoje nenhum estado/sessão compartilhado entre a geração de
    # dois endpoints (cada chamada a generate_endpoint cria sua própria
    # VariableResolutionSession internamente) — gerar A com uma
    # variable_extraction para "customer_id" não torna "customer_id"
    # resolvível para B. Isso não é uma garantia de isolamento
    # deliberadamente projetada; é consequência direta de
    # variable_extractions nunca ser lido pelo gerador Playwright (ver
    # teste 2) — não há CANAL nenhum por onde um valor vazaria de um
    # endpoint para o outro.
    strategy_source_a, normalized_request_a = _analyzed(_REQUEST_A)
    strategy_a = TestStrategy(
        endpoint_source=strategy_source_a.source,
        assertions=_STATUS_ASSERTIONS,
        variable_extractions=(
            VariableExtraction(
                variable_name="customer_id",
                source="response.body",
                json_path="$.id",
                scope=VariableScope.COLLECTION,
                origin="contract",
            ),
        ),
        negative_cases=(),
        warnings=(),
    )
    PlaywrightEndpointTestGenerator().generate_endpoint(strategy_a, normalized_request_a)

    strategy_source_b, normalized_request_b = _analyzed(_REQUEST_B)
    strategy_b = TestStrategy(
        endpoint_source=strategy_source_b.source,
        assertions=_STATUS_ASSERTIONS,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    generated_b = PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy_b, normalized_request_b
    )

    # B continua sem resolução mesmo depois de A ter "declarado" a
    # extração — nenhum vazamento, mas também nenhuma cadeia funcional.
    assert len(generated_b.unresolved_variables) == 1
    assert generated_b.unresolved_variables[0].name == "customer_id"
