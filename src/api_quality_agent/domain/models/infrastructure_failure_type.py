from enum import Enum


class InfrastructureFailureType(str, Enum):
    EXECUTABLE_NOT_FOUND = "executable_not_found"
    TIMEOUT = "timeout"
    INVALID_COLLECTION = "invalid_collection"
    UNEXPECTED_ERROR = "unexpected_error"
    # PlaywrightAdapter: caminho da suíte gerada (diretório) inexistente ou
    # não é um diretório — pré-validação antes de subir o processo, mesmo
    # espírito de INVALID_COLLECTION para o Newman. Um valor próprio (em vez
    # de reaproveitar INVALID_COLLECTION) porque esse nome já carrega
    # terminologia específica do Postman e seria enganoso aqui.
    TEST_SUITE_NOT_FOUND = "test_suite_not_found"
    # PlaywrightAdapter: pytest terminou com exit code 5 ("no tests were
    # collected" — diretório válido, processo rodou, mas nenhuma função de
    # teste foi encontrada). Diferente de TEST_SUITE_NOT_FOUND (caminho
    # inválido, detectado ANTES de subir o processo): aqui o processo
    # completou normalmente, só não achou nada pra rodar — nunca reportado
    # como total_requests=0/success=false silenciosamente, sempre como esta
    # falha de infraestrutura explícita, com o exit code original preservado.
    NO_TESTS_COLLECTED = "no_tests_collected"
