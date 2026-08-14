from dataclasses import dataclass


@dataclass(frozen=True)
class EnvironmentVariable:
    # Uma entrada de "values" de um Environment do Postman. is_secret vem
    # da evidência estrutural ("type": "secret"), nunca do nome da
    # variável — mesmo critério já usado por NewmanAdapter para mascarar
    # saída do Newman.
    key: str
    value: str
    is_secret: bool
    enabled: bool
    # De onde este valor veio: "environment" (um Environment de verdade,
    # informado via -e/--environment) ou "collection" (o array `variable[]`
    # de nível de Collection, mesclado por
    # generators.playwright.variable_resolver.merge_collection_variables).
    # Default "environment" preserva todo construtor existente sem
    # precisar declarar o campo — nunca reordenado nem usado para decidir
    # precedência sozinho (a ordem de merge já garante Environment antes de
    # Collection); só para rastreabilidade, nunca misturando os dois de
    # forma indistinguível.
    source: str = "environment"
