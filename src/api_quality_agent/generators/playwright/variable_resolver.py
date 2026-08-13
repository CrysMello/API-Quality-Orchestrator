"""Parte 15 do plano de ação Playwright: resolvedor central de
{{variáveis}}, reaproveitado por todo campo que precise delas (Partes 12 e
14 já resolviam variável cada um à sua maneira — este módulo unifica isso
para que o mesmo nome tenha o mesmo tratamento em qualquer campo, deduplica
entre campos, e alimenta o rastreamento usado pelo generation-manifest.json).

Prioridade de resolução (determinística, nunca invertida):
1. environment informado no `generate` (-e/--environment) — só quando a
   variável NÃO é secret (ver EnvironmentVariable.is_secret) e tem valor.
2. valor literal já declarado na própria Collection (hoje, o único lugar
   onde isso existe estruturalmente é `url.variable[]` — o default que o
   Postman guarda ao lado de um segmento de path `:nome`/`{nome}`).
3. variável de ambiente do sistema — nunca lida agora: o código gerado lê
   `os.environ.get("AQO_<NOME>")` quando o teste roda de verdade. É o que
   sempre acontece quando 1 e 2 não resolveram (secret incluído — nunca o
   valor literal do secret, só o nome da variável externa).
4. variável de workflow (produzida por outro teste) — fora de escopo desta
   fase (ver "Não implementar"), nunca considerada aqui.
5. não resolvida — decisão de quem chama este módulo, não deste módulo:
   uma referência parcial ({{}} misturada com texto) ou um segmento
   `:nome`/`{nome}` sem default na Collection nunca chegam a
   `resolve`/`resolve_compile_time` — o campo desiste antes, registrando
   `mark_unresolved`.
"""

import json
import re
from dataclasses import dataclass, field

from api_quality_agent.domain.models import PostmanEnvironment
from api_quality_agent.generators.playwright.endpoint_file_naming import to_snake_case

UNRESOLVED_VARIABLE = "UNRESOLVED_VARIABLE"

_ENV_VAR_PREFIX = "AQO_"
_UPLOAD_ENV_VAR_INFIX = "UPLOAD_"

# Só uma referência de variável Postman, nada mais na string (ex.:
# "{{accessToken}}" resolve; "Bearer {{accessToken}}" ou um valor literal
# não resolvem) — mesmo critério conservador usado desde a Parte 12: nunca
# tenta interpolar uma variável dentro de um texto maior.
_PURE_VARIABLE_REFERENCE = re.compile(r"^\{\{\s*([^{}]+?)\s*\}\}$")

# Nome de campo/variável pode ter espaço/símbolo (ex.: "Profile Picture") —
# nunca usado cru como identificador Python nem como sufixo de variável de
# ambiente (mesmo alfabeto seguro de endpoint_file_naming._sanitize_segment).
_INVALID_IDENTIFIER_CHARS = re.compile(r"[^a-z0-9_]+")


def extract_pure_variable_name(value: str | None) -> str | None:
    if not value:
        return None
    match = _PURE_VARIABLE_REFERENCE.match(value)
    return match.group(1) if match else None


def sanitize_identifier(name: str) -> str:
    # Reserva de nome quando sobra vazio (ex.: nome só com símbolos) nunca
    # deixa o identificador em branco.
    #
    # Limitação conhecida, deliberadamente não tratada (sem exemplo/critério
    # que peça isso): dois nomes que sanitizam para o mesmo slug (ex.:
    # "Profile Picture" e "profile_picture") colidiriam no mesmo identificador
    # — o último declarado prevalece, sem aviso.
    slug = _INVALID_IDENTIFIER_CHARS.sub("_", to_snake_case(name)).strip("_")
    return slug or "field"


def to_env_var_name(variable_name: str) -> str:
    # apiKey -> api_key -> AQO_API_KEY; accessToken -> AQO_ACCESS_TOKEN.
    return f"{_ENV_VAR_PREFIX}{sanitize_identifier(variable_name).upper()}"


def multipart_file_env_var(field_key: str) -> str:
    # Nunca deriva do "src" declarado na Collection (normalmente um caminho
    # local da máquina de quem criou a Collection) — só do nome do campo
    # ("Receber caminhos de arquivos por configuração ou variável de
    # ambiente", Parte 14).
    return f"{_ENV_VAR_PREFIX}{_UPLOAD_ENV_VAR_INFIX}{sanitize_identifier(field_key).upper()}"


def python_string_literal(value: str) -> str:
    # json.dumps produz um literal de string válido em Python (aspas duplas,
    # escapes compatíveis) — ensure_ascii=False preserva acentuação/unicode
    # como texto legível no código gerado, mesma convenção já usada em todo
    # o restante do gerador.
    return json.dumps(value, ensure_ascii=False)


def env_var_lookup_lines(local_variable: str, env_var: str) -> tuple[str, ...]:
    # "Validação clara de variável obrigatória": o teste falha explicando
    # exatamente qual variável de ambiente configurar, em vez de um erro
    # genérico vindo de dentro do Playwright.
    return (
        f'    {local_variable} = os.environ.get("{env_var}")\n',
        f'    assert {local_variable}, '
        f'"Variável de ambiente obrigatória {env_var} não definida."\n',
    )


@dataclass(frozen=True)
class UnresolvedVariable:
    # Uma entrada do manifesto (ver default_playwright_test_suite_builder.py)
    # — nunca inclui o endpoint aqui (a sessão é por endpoint; quem monta o
    # manifesto já sabe a qual endpoint esta lista pertence).
    name: str
    location: str


@dataclass
class VariableResolutionSession:
    # Estado acumulado ao longo da geração de UM endpoint — nunca
    # compartilhado entre endpoints diferentes (cada um tem sua própria
    # sessão; dedup de nome de variável local só faz sentido dentro do
    # mesmo arquivo de teste gerado).
    environment: PostmanEnvironment | None = None
    seen_local_names: set[str] = field(default_factory=set)
    preamble_lines: list[str] = field(default_factory=list)
    extra_imports: set[str] = field(default_factory=set)
    # AQO_* realmente referenciadas pelo código gerado deste endpoint —
    # tanto por variável deferida (prioridade 3) quanto por secret (nunca o
    # valor, só o nome — "Secrets não aparecem no código nem no manifesto").
    required_environment_variables: set[str] = field(default_factory=set)
    # nome Postman -> valor literal resolvido (nunca secret) — usado pelo
    # manifesto ("variáveis resolvidas sem expor secrets").
    resolved_variables: dict[str, str] = field(default_factory=dict)
    unresolved: list[UnresolvedVariable] = field(default_factory=list)

    def _environment_value(self, name: str) -> tuple[str, bool] | None:
        # (valor, is_secret) só quando o Environment define e habilita a
        # variável; None quando não define (não é "resolvida como vazia").
        if self.environment is None:
            return None
        variable = self.environment.get(name)
        if variable is None:
            return None
        return variable.value, variable.is_secret

    def _literal_value(self, name: str, *, collection_literal: str | None) -> str | None:
        # Prioridade 1 então 2 — só um valor NÃO secret conta como
        # "literal conhecido na geração"; secret sempre pula direto para a
        # prioridade 3 (nunca embutido, mesmo já sabendo o valor).
        environment_hit = self._environment_value(name)
        if environment_hit is not None:
            value, is_secret = environment_hit
            if is_secret:
                return None
            if value:
                self.resolved_variables[name] = value
                return value
        if collection_literal:
            self.resolved_variables[name] = collection_literal
            return collection_literal
        return None

    def _defer(self, name: str, local_variable: str) -> str:
        # Prioridade 3: variável de ambiente do sistema, lida só quando o
        # teste roda — nunca agora. Sempre construível para qualquer nome,
        # secret ou não (secret cai direto aqui, pulando a prioridade 1/2).
        env_var = to_env_var_name(name)
        self.required_environment_variables.add(env_var)
        if local_variable not in self.seen_local_names:
            self.seen_local_names.add(local_variable)
            self.preamble_lines.extend(env_var_lookup_lines(local_variable, env_var))
            self.extra_imports.add("os")
        return local_variable

    def resolve(self, name: str, *, collection_literal: str | None = None) -> str:
        # Expressão Python já pronta para embutir INLINE no código gerado —
        # um literal escapado (prioridade 1/2, valor já conhecido na
        # geração) ou o nome de uma variável local lida em runtime
        # (prioridade 3). O identificador local é sempre derivado do
        # próprio nome da variável Postman (sanitize_identifier) — nunca
        # escolhido pelo chamador — para que o MESMO nome usado em dois
        # campos diferentes (ex.: {{token}} num header e numa query)
        # resolva para a MESMA variável local, sem duplicar preâmbulo.
        literal = self._literal_value(name, collection_literal=collection_literal)
        if literal is not None:
            return python_string_literal(literal)
        return self._defer(name, sanitize_identifier(name))

    def resolve_as_local_variable(
        self, name: str, local_variable: str, *, collection_literal: str | None = None
    ) -> str:
        # Como resolve(), mas SEMPRE materializa {{name}} numa variável
        # local com o nome fixo pedido — mesmo quando o valor já é
        # conhecido na geração (literal). Usado quando o valor entra numa
        # composição maior em runtime (ex.: f"Bearer {token}", base64 de
        # username:password — Parte 12) e por isso precisa sempre existir
        # como identificador, nunca como literal inline solto.
        if local_variable in self.seen_local_names:
            return local_variable

        literal = self._literal_value(name, collection_literal=collection_literal)
        if literal is not None:
            self.seen_local_names.add(local_variable)
            self.preamble_lines.append(
                f"    {local_variable} = {python_string_literal(literal)}\n"
            )
            return local_variable
        return self._defer(name, local_variable)

    def resolve_compile_time(
        self, name: str, *, collection_literal: str | None = None
    ) -> str | None:
        # Só prioridades 1 e 2 (valor já conhecido na geração, sem esperar o
        # teste rodar) — usado por campos que não têm como virar uma
        # expressão em runtime (path/base URL: o path= do Playwright é
        # sempre uma string simples neste gerador, nunca uma f-string —
        # limitação deliberada desta parte, ver _resolve_path_segments em
        # playwright_endpoint_test_generator.py). Retorna o VALOR BRUTO
        # (não um literal de código já escapado) para o chamador decidir
        # como embuti-lo; None quando não há valor literal disponível
        # (nunca defere para AQO_* aqui).
        return self._literal_value(name, collection_literal=collection_literal)

    def resolve_file_field(self, field_key: str, preamble_builder) -> str:
        # Campo de arquivo multipart (Parte 14): nunca resolve a partir de
        # {{variável}} — o env var vem sempre do nome do campo
        # (AQO_UPLOAD_<CAMPO>), nunca do "src" declarado na Collection.
        # preamble_builder(field_key, local_name) -> tuple[str, ...] é
        # injetado pelo chamador para não duplicar aqui o formato
        # específico de FilePayload (responsabilidade do gerador).
        local_name = sanitize_identifier(field_key)
        env_var = multipart_file_env_var(field_key)
        self.required_environment_variables.add(env_var)
        if local_name not in self.seen_local_names:
            self.seen_local_names.add(local_name)
            self.preamble_lines.extend(preamble_builder(field_key, local_name))
            self.extra_imports |= {"os", "pytest", "mimetypes"}
        return local_name

    def mark_unresolved(self, name: str, location: str) -> None:
        # Nunca vira um PlaywrightGenerationWarning aqui — quem consome isso
        # é sempre o manifesto (default_playwright_test_suite_builder.py),
        # que monta a entrada rica {"code": UNRESOLVED_VARIABLE, "variable":
        # ..., "location": ...} diretamente a partir de
        # GeneratedEndpointTest.unresolved_variables.
        self.unresolved.append(UnresolvedVariable(name=name, location=location))
