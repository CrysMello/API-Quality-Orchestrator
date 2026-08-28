from dataclasses import dataclass


@dataclass(frozen=True)
class VariableUsage:
    # Representa que ESTE endpoint (o dono da TestStrategy que carrega esta
    # entrada) consome, em runtime, um valor produzido por OUTRO endpoint —
    # nunca resolvido via {{variável}} do Postman, nunca um literal
    # conhecido na geração (ver VariableResolutionSession). A relação é
    # sempre por par (producer_test_id, variable_name) — nunca só pelo
    # nome, exatamente para dois produtores diferentes usando o mesmo nome
    # de variável nunca colidirem (ver endpoint_dependency_linking.py).
    variable_name: str
    # test_id do endpoint produtor (mesmo identificador usado em
    # AssertionResult.test_id/HttpTransaction.test_id — o nome da função
    # de teste gerada) — nunca um índice, nunca o endpoint_source cru.
    producer_test_id: str
    # Onde, no request DESTE endpoint, o valor é consumido — "path" é o
    # único local implementado nesta etapa (ver playwright_endpoint_test_
    # generator.py); "query"/"header"/"body" ficam previstos na estrutura
    # para uma extensão futura, nunca implementados aqui.
    location: str
