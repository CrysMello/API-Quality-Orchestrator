"""Parte 24 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
catálogo estruturado e estável de códigos de PlaywrightGenerationWarning —
a ÚNICA fonte de verdade para cada `code` possível (regra 1: "warning não
pode ser apenas texto livre").

Este módulo não importa nada de outro módulo do gerador Playwright
(deliberado, evita import circular: playwright_generation_warning.py
importa DESTE módulo para validar `code` em __post_init__, e os módulos que
constroem warnings — playwright_endpoint_test_generator.py,
variable_resolver.py, endpoint_file_naming.py — importam os códigos DESTE
módulo em vez de redefini-los). Cada módulo que já expunha um destes nomes
(ex.: `playwright_endpoint_test_generator.EXPECTED_STATUS_NOT_DEFINED`)
continua expondo o MESMO nome, só reexportado a partir daqui — nenhum
import existente (interno ou de teste) quebra.
"""

# --- Endpoint / request não suportado (Partes 07-15) -------------------------

# Parte 24: HTTP_METHOD_NOT_SUPPORTED e URL_NOT_RESOLVED substituem, nos dois
# casos em que cada um se aplica, o antigo código genérico
# ENDPOINT_NOT_SUPPORTED_YET — mesmo espírito da Parte 23 reclassificando um
# fallback já existente, nunca um comportamento novo (o endpoint continua
# caindo no mesmo PlaceholderEndpointTestGenerator de sempre).
HTTP_METHOD_NOT_SUPPORTED = "HTTP_METHOD_NOT_SUPPORTED"
URL_NOT_RESOLVED = "URL_NOT_RESOLVED"
BODY_NOT_SUPPORTED = "BODY_NOT_SUPPORTED"
BODY_JSON_INVALID = "BODY_JSON_INVALID"
MULTIPART_FILE_NOT_RESOLVED = "MULTIPART_FILE_NOT_RESOLVED"
AUTHENTICATION_NOT_SUPPORTED = "AUTHENTICATION_NOT_SUPPORTED"
AUTHENTICATION_VALUE_NOT_RESOLVED = "AUTHENTICATION_VALUE_NOT_RESOLVED"
# Mantido no catálogo por estabilidade (código estável, regra do critério de
# aceite — um manifesto já gerado antes da Parte 24 pode tê-lo registrado),
# mas nenhum caminho de geração atual o emite mais: os dois casos que ele
# cobria (método/URL) agora têm código próprio e mais específico, acima.
ENDPOINT_NOT_SUPPORTED_YET = "ENDPOINT_NOT_SUPPORTED_YET"

# --- Headers (Parte 11) --------------------------------------------------------

HEADER_VALUE_NOT_RESOLVED = "HEADER_VALUE_NOT_RESOLVED"
SENSITIVE_HEADER_OMITTED = "SENSITIVE_HEADER_OMITTED"
RESERVED_HEADER_OMITTED = "RESERVED_HEADER_OMITTED"
DUPLICATE_HEADER_IGNORED = "DUPLICATE_HEADER_IGNORED"

# --- Asserções inteligentes (Bloco 4, Partes 16-23) ---------------------------

EXPECTED_STATUS_NOT_DEFINED = "EXPECTED_STATUS_NOT_DEFINED"
BODY_STRUCTURE_NOT_DETERMINED = "BODY_STRUCTURE_NOT_DETERMINED"
BROAD_STATUS_ASSERTION = "BROAD_STATUS_ASSERTION"
JSON_SCHEMA_REF_NOT_SUPPORTED = "JSON_SCHEMA_REF_NOT_SUPPORTED"
# Parte 24: uma expectativa PONTUAL (um campo, um item de array) tinha
# evidência de que uma asserção poderia existir (ex.: "type" declarado),
# mas o valor não pôde virar uma checagem sem arbitrariedade (ex.: lista
# ambígua de tipos) — mais granular que os warnings de categoria acima, que
# cobrem a asserção inteira faltando.
ASSERTION_NOT_GENERATED = "ASSERTION_NOT_GENERATED"
# Parte 24: havia uma intenção/indício declarado no contrato (ex.:
# x-source-request-field apontando para um campo do request), mas
# informação insuficiente em tempo de geração para confirmá-lo (o campo
# apontado nunca foi de fato enviado nesta requisição) — distinto de
# ASSERTION_NOT_GENERATED (schema ambíguo) e de BROAD (aproximação
# deliberadamente permitida e documentada): aqui nada é gerado, nem
# aproximado, só registrado para investigação.
INFORMATION_INSUFFICIENT = "INFORMATION_INSUFFICIENT"

# --- Variáveis e arquivos (Partes 05, 15) -------------------------------------

UNRESOLVED_VARIABLE = "UNRESOLVED_VARIABLE"
FILE_NAME_COLLISION_RESOLVED = "FILE_NAME_COLLISION_RESOLVED"

# Descrição curta só para leitura humana (documentação/depuração/relatório
# futuro) — nunca usada como a mensagem do warning em si, que é sempre
# escrita evidência a evidência no ponto de geração (mais específica que
# esta descrição genérica do código).
PLAYWRIGHT_WARNING_CODE_DESCRIPTIONS: dict[str, str] = {
    HTTP_METHOD_NOT_SUPPORTED: "Método HTTP do request ainda não suportado pela geração real.",
    URL_NOT_RESOLVED: "URL (host, path ou query) contém variável(is) sem resolução conhecida.",
    BODY_NOT_SUPPORTED: "Modo/Content-Type do body do request ainda não suportado.",
    BODY_JSON_INVALID: "Body declarado como JSON, mas o conteúdo não é um JSON válido.",
    MULTIPART_FILE_NOT_RESOLVED: "Campo de arquivo multipart sem nome (key) resolvível.",
    AUTHENTICATION_NOT_SUPPORTED: "Tipo de autenticação ainda não suportado pela geração real.",
    AUTHENTICATION_VALUE_NOT_RESOLVED: (
        "Autenticação suportada, mas o valor não é uma referência de variável resolvível."
    ),
    ENDPOINT_NOT_SUPPORTED_YET: (
        "Predecessor genérico de HTTP_METHOD_NOT_SUPPORTED/URL_NOT_RESOLVED — nenhum caminho "
        "de geração atual o emite mais."
    ),
    HEADER_VALUE_NOT_RESOLVED: "Valor de header contém variável parcial não resolvível.",
    SENSITIVE_HEADER_OMITTED: "Header sensível (ex.: Authorization) omitido do código gerado.",
    RESERVED_HEADER_OMITTED: "Header reservado para geração futura, omitido por enquanto.",
    DUPLICATE_HEADER_IGNORED: "Header repetido (mesmo nome, case-insensitive); só o último ficou.",
    EXPECTED_STATUS_NOT_DEFINED: "Nenhum status HTTP esperado determinável por evidência.",
    BODY_STRUCTURE_NOT_DETERMINED: (
        "Body é JSON válido, mas a estrutura do nível superior é desconhecida."
    ),
    BROAD_STATUS_ASSERTION: "Asserção de status classificada como aproximação (BROAD).",
    JSON_SCHEMA_REF_NOT_SUPPORTED: "Schema contém $ref remoto/não local; validação completa pulada.",
    ASSERTION_NOT_GENERATED: (
        "Evidência parcial para uma expectativa pontual; nenhuma asserção gerada para ela."
    ),
    INFORMATION_INSUFFICIENT: (
        "Intenção declarada no contrato sem informação suficiente em tempo de geração para "
        "confirmá-la."
    ),
    UNRESOLVED_VARIABLE: "Variável {{...}} referenciada sem valor resolvível.",
    FILE_NAME_COLLISION_RESOLVED: "Nome de arquivo gerado colidiu com outro; sufixo aplicado.",
}

# Conjunto estável usado para validar `code` em PlaywrightGenerationWarning
# (regra 1) — qualquer novo código precisa ser registrado aqui antes de
# poder ser emitido em qualquer lugar do gerador.
PLAYWRIGHT_WARNING_CODES: frozenset[str] = frozenset(PLAYWRIGHT_WARNING_CODE_DESCRIPTIONS)
