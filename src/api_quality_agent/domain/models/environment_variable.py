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
