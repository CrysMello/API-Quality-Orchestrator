"""Parte 16 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
geração de asserções de status HTTP orientadas exclusivamente por evidência
já disponível em strategy.assertions (a mesma TestStrategyEngine reaproveitada
do caminho Postman) — nunca 200/201 assumido, nunca um range/`.ok` no lugar
de um valor exato desconhecido.

`assert response.status == N` só aparece quando existe uma
AssertionDefinition(STATUS_CODE) resolvida por evidência (estratégia de
teste, Postman, OpenAPI, contrato ou exemplo); na ausência dela, o cenário
mantém a validação temporária do Bloco 3 ("assert response is not None") e
ganha o warning EXPECTED_STATUS_NOT_DEFINED, rastreável até o
generation-manifest.json (Parte 15).
"""

import ast
import json

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine, TestStrategyEngine
from api_quality_agent.generators.playwright import (
    EXPECTED_STATUS_NOT_DEFINED,
    PlaywrightEndpointTestGenerator,
)
from api_quality_agent.generators.postman_test_generator import PostmanTestGenerator
from api_quality_agent.parsers import PostmanCollectionParser

_GET_USERS = {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}


def _status_assertion(status_code: int, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
        origin=origin,
    )


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


def _generate(request: dict, assertions: tuple[AssertionDefinition, ...]):
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)


# --- status explícito: um assert exato por código ---------------------------


def test_explicit_status_200_generates_an_exact_assertion():
    generated = _generate(_GET_USERS, (_status_assertion(200),))

    assert "assert response.status == 200" in generated.content
    assert "assert response is not None" not in generated.content
    assert generated.warnings == ()
    ast.parse(generated.content)


def test_explicit_status_201_generates_an_exact_assertion():
    generated = _generate(_GET_USERS, (_status_assertion(201),))

    assert "assert response.status == 201" in generated.content
    ast.parse(generated.content)


def test_explicit_status_204_generates_an_exact_assertion():
    generated = _generate(_GET_USERS, (_status_assertion(204),))

    assert "assert response.status == 204" in generated.content
    ast.parse(generated.content)


def test_explicit_status_400_generates_an_exact_assertion():
    # Nunca "sucesso assumido": a única evidência documentada sendo 400
    # ainda gera exatamente 400, não um 200 inventado para o cenário
    # "success".
    generated = _generate(_GET_USERS, (_status_assertion(400),))

    assert "assert response.status == 400" in generated.content
    assert "200" not in generated.content
    ast.parse(generated.content)


def test_explicit_status_422_generates_an_exact_assertion():
    generated = _generate(_GET_USERS, (_status_assertion(422),))

    assert "assert response.status == 422" in generated.content
    ast.parse(generated.content)


# --- ausência de status confiável: nunca inventa, registra warning ---------


def test_absence_of_known_status_keeps_the_temporary_assertion():
    generated = _generate(_GET_USERS, ())

    assert "assert response is not None" in generated.content
    assert "response.status ==" not in generated.content
    ast.parse(generated.content)


def test_absence_of_known_status_never_invents_200_or_any_other_code():
    generated = _generate(_GET_USERS, ())

    for guessed_code in ("200", "201", "204"):
        assert guessed_code not in generated.content


def test_absence_of_known_status_registers_a_traceable_warning():
    generated = _generate(_GET_USERS, ())

    # Parte 23: junto de EXPECTED_STATUS_NOT_DEFINED (por que não há
    # evidência), também BROAD_STATUS_ASSERTION (o que foi gerado no lugar
    # é uma aproximação) — os dois sempre coexistem, nunca um no lugar do
    # outro.
    assert len(generated.warnings) == 2
    codes = {warning.code for warning in generated.warnings}
    assert codes == {"EXPECTED_STATUS_NOT_DEFINED", "BROAD_STATUS_ASSERTION"}
    warning = next(w for w in generated.warnings if w.code == EXPECTED_STATUS_NOT_DEFINED)
    assert warning.endpoint == "GET /users"
    assert warning.scenario == "success"


def test_absence_of_known_status_is_flagged_as_partial_in_the_docstring():
    generated = _generate(_GET_USERS, ())

    assert "validação parcial" in generated.content


# --- origem registrada nos metadados (preparação para EXACT/DERIVED/BROAD) -


def test_status_origin_is_recorded_in_the_docstring():
    generated = _generate(_GET_USERS, (_status_assertion(201, origin="example"),))

    assert "Status: 201 (origem: example)" in generated.content


# --- nunca um range/classe HTTP no lugar de um valor exato ------------------


def test_never_generates_a_range_or_ok_based_assertion():
    for status in (200, 201, 204, 400, 422):
        generated = _generate(_GET_USERS, (_status_assertion(status),))
        assert "response.ok" not in generated.content
        assert "range(" not in generated.content
        assert "<= response.status" not in generated.content
        assert "response.status <" not in generated.content


def test_client_and_auth_errors_are_never_generated_as_success_by_http_class():
    # 401/403/404/415 continuam exatos — nunca reclassificados como sucesso
    # só por "pertencerem" a alguma faixa aceitável.
    for status in (401, 403, 404, 415):
        generated = _generate(_GET_USERS, (_status_assertion(status),))
        assert f"assert response.status == {status}" in generated.content
        assert "response.ok" not in generated.content


# --- múltiplos responses: pipeline completo escolhe o primeiro 2xx ---------


def test_multiple_responses_pick_the_first_success_code_end_to_end():
    # Prova a fiação ponta a ponta (ApiAnalysisEngine -> TestStrategyEngine
    # -> PlaywrightEndpointTestGenerator): dois Examples salvos (201 e 400)
    # no mesmo request — TestStrategyEngine já escolhe o primeiro código
    # 2xx (ver test_strategy_engine.py); aqui confirmamos que essa escolha
    # chega intacta ao teste Playwright gerado.
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Collection",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": [
                    {
                        "name": "Create user",
                        "id": "r1",
                        "request": {"method": "POST", "url": "https://api.exemplo.com/users"},
                        "response": [
                            {
                                "name": "Created",
                                "originalRequest": {
                                    "method": "POST",
                                    "url": "https://api.exemplo.com/users",
                                },
                                "code": 201,
                                "header": [],
                            },
                            {
                                "name": "Bad request",
                                "originalRequest": {
                                    "method": "POST",
                                    "url": "https://api.exemplo.com/users",
                                },
                                "code": 400,
                                "header": [],
                            },
                        ],
                    }
                ],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    strategy = TestStrategyEngine().build_strategy(analyzed.analysis)

    generated = PlaywrightEndpointTestGenerator().generate_endpoint(
        strategy, analyzed.normalized_request
    )

    assert "assert response.status == 201" in generated.content
    assert "400" not in generated.content
    assert generated.warnings == ()
    ast.parse(generated.content)


# --- fluxo Postman preservado -------------------------------------------------


def test_postman_flow_keeps_generating_the_same_status_assertion():
    # Nenhuma mudança no gerador Postman — mesma TestStrategy, mesma
    # asserção pm.response.to.have.status(...) de sempre.
    strategy = TestStrategy(
        endpoint_source="GET /users",
        assertions=(_status_assertion(201),),
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )

    generated = PostmanTestGenerator().generate(strategy)

    assert "pm.response.to.have.status(201);" in generated.script


def test_postman_flow_keeps_the_ambiguous_status_warning_vocabulary():
    from api_quality_agent.domain.models import StrategyWarning

    strategy = TestStrategy(
        endpoint_source="GET /users",
        assertions=(),
        variable_extractions=(),
        negative_cases=(),
        warnings=(
            StrategyWarning(
                code="STATUS_CODE_AMBIGUOUS",
                message="Nenhum status code documentado.",
                endpoint="GET /users",
            ),
        ),
    )

    generated = PostmanTestGenerator().generate(strategy)

    assert any(w.code == "EXPECTED_STATUS_NOT_DEFINED" for w in generated.warnings)


# --- geração sintaticamente válida em todo cenário --------------------------


def test_all_status_scenarios_produce_syntactically_valid_python():
    scenarios = (
        (),
        (_status_assertion(200),),
        (_status_assertion(201),),
        (_status_assertion(204),),
        (_status_assertion(400),),
        (_status_assertion(422),),
    )
    for assertions in scenarios:
        generated = _generate(_GET_USERS, assertions)
        ast.parse(generated.content)
