"""Parte 15 do plano de ação Playwright: testes unitários puros do
resolvedor central de {{variáveis}} (variable_resolver.py), independentes
do gerador Playwright — a prioridade determinística (environment -> literal
da Collection -> variável de ambiente do sistema -> nunca inventa) e a
deduplicação entre campos são testadas aqui diretamente na sessão, sem
precisar montar um NormalizedRequest inteiro.
"""

from api_quality_agent.domain.models import EnvironmentVariable, PostmanEnvironment
from api_quality_agent.generators.playwright.variable_resolver import (
    VariableResolutionSession,
    extract_pure_variable_name,
    multipart_file_env_var,
    sanitize_identifier,
    to_env_var_name,
)


def _environment(**variables: tuple[str, bool]) -> PostmanEnvironment:
    # variables: nome -> (valor, is_secret), todas habilitadas.
    return PostmanEnvironment(
        name="QA",
        variables=tuple(
            EnvironmentVariable(key=key, value=value, is_secret=is_secret, enabled=True)
            for key, (value, is_secret) in variables.items()
        ),
    )


# --- extract_pure_variable_name / identificadores ---------------------------


def test_extract_pure_variable_name_matches_the_whole_string():
    assert extract_pure_variable_name("{{accessToken}}") == "accessToken"


def test_extract_pure_variable_name_rejects_partial_reference():
    assert extract_pure_variable_name("Bearer {{accessToken}}") is None


def test_extract_pure_variable_name_rejects_plain_literal():
    assert extract_pure_variable_name("abc123") is None


def test_extract_pure_variable_name_handles_none_and_empty():
    assert extract_pure_variable_name(None) is None
    assert extract_pure_variable_name("") is None


def test_to_env_var_name_converts_camel_case():
    assert to_env_var_name("accessToken") == "AQO_ACCESS_TOKEN"
    assert to_env_var_name("apiKey") == "AQO_API_KEY"


def test_multipart_file_env_var_uses_upload_infix():
    assert multipart_file_env_var("avatar") == "AQO_UPLOAD_AVATAR"


def test_sanitize_identifier_replaces_spaces_and_symbols():
    assert sanitize_identifier("Profile Picture") == "profile_picture"
    assert sanitize_identifier("!!!") == "field"


# --- prioridade 1: environment (não secret) ---------------------------------


def test_resolve_uses_non_secret_environment_value_as_a_literal():
    session = VariableResolutionSession(environment=_environment(baseUrl=("api.exemplo.com", False)))

    expression = session.resolve("baseUrl")

    assert expression == '"api.exemplo.com"'
    assert session.resolved_variables == {"baseUrl": "api.exemplo.com"}
    assert session.required_environment_variables == set()
    assert session.preamble_lines == []


def test_resolve_never_embeds_a_secret_environment_value():
    session = VariableResolutionSession(environment=_environment(accessToken=("super-secreto", True)))

    expression = session.resolve("accessToken")

    assert "super-secreto" not in expression
    assert expression == "access_token"
    assert session.resolved_variables == {}
    assert session.required_environment_variables == {"AQO_ACCESS_TOKEN"}
    assert "super-secreto" not in "".join(session.preamble_lines)


# --- prioridade 2: literal da Collection -------------------------------------


def test_resolve_falls_back_to_collection_literal_when_environment_has_no_match():
    session = VariableResolutionSession(environment=_environment(other=("x", False)))

    expression = session.resolve("id", collection_literal="42")

    assert expression == '"42"'
    assert session.resolved_variables == {"id": "42"}
    assert session.required_environment_variables == set()


def test_environment_takes_priority_over_collection_literal():
    session = VariableResolutionSession(environment=_environment(id=("99", False)))

    expression = session.resolve("id", collection_literal="42")

    assert expression == '"99"'
    assert session.resolved_variables == {"id": "99"}


# --- prioridade 3: variável de ambiente do sistema (deferida) --------------


def test_resolve_defers_to_a_system_environment_variable_without_any_source():
    session = VariableResolutionSession(environment=None)

    expression = session.resolve("accessToken")

    assert expression == "access_token"
    assert session.required_environment_variables == {"AQO_ACCESS_TOKEN"}
    assert 'access_token = os.environ.get("AQO_ACCESS_TOKEN")' in "".join(session.preamble_lines)
    assert (
        'assert access_token, "Variável de ambiente obrigatória AQO_ACCESS_TOKEN não definida."'
        in "".join(session.preamble_lines)
    )
    assert session.extra_imports == {"os"}


def test_resolve_never_invents_a_value_when_environment_has_no_variables_at_all():
    session = VariableResolutionSession(environment=PostmanEnvironment(name="QA", variables=()))

    expression = session.resolve("missing")

    assert expression == "missing"
    assert session.resolved_variables == {}
    assert session.required_environment_variables == {"AQO_MISSING"}


def test_disabled_environment_variable_is_never_used():
    environment = PostmanEnvironment(
        name="QA",
        variables=(EnvironmentVariable(key="baseUrl", value="x", is_secret=False, enabled=False),),
    )
    session = VariableResolutionSession(environment=environment)

    expression = session.resolve("baseUrl")

    assert expression == "base_url"
    assert session.resolved_variables == {}


# --- dedup: mesmo nome, mesma variável local, preâmbulo uma vez só ----------


def test_resolve_deduplicates_the_same_variable_used_twice():
    session = VariableResolutionSession(environment=None)

    first = session.resolve("token")
    second = session.resolve("token")

    assert first == second == "token"
    assert len(session.preamble_lines) == 2  # só um par (lookup + assert)


def test_resolve_deduplicates_across_two_different_env_var_derived_names_only_if_equal():
    # Nomes Postman diferentes nunca colidem por acidente (ao menos quando
    # já são identificadores válidos) — cada um vira sua própria variável.
    session = VariableResolutionSession(environment=None)

    session.resolve("accessToken")
    session.resolve("refreshToken")

    assert session.required_environment_variables == {"AQO_ACCESS_TOKEN", "AQO_REFRESH_TOKEN"}
    assert len(session.preamble_lines) == 4


# --- resolve_as_local_variable: sempre materializa, mesmo com literal ------


def test_resolve_as_local_variable_materializes_a_literal_value_too():
    session = VariableResolutionSession(environment=_environment(accessToken=("abc123", False)))

    expression = session.resolve_as_local_variable("accessToken", "token")

    assert expression == "token"
    assert 'token = "abc123"' in "".join(session.preamble_lines)
    assert session.resolved_variables == {"accessToken": "abc123"}


def test_resolve_as_local_variable_defers_when_secret():
    session = VariableResolutionSession(environment=_environment(accessToken=("abc123", True)))

    expression = session.resolve_as_local_variable("accessToken", "token")

    assert expression == "token"
    assert 'os.environ.get("AQO_ACCESS_TOKEN")' in "".join(session.preamble_lines)
    assert "abc123" not in "".join(session.preamble_lines)


def test_resolve_as_local_variable_deduplicates_by_local_name():
    session = VariableResolutionSession(environment=None)

    session.resolve_as_local_variable("accessToken", "token")
    session.resolve_as_local_variable("accessToken", "token")

    assert len(session.preamble_lines) == 2


# --- resolve_compile_time: só 1 e 2, nunca defere ---------------------------


def test_resolve_compile_time_returns_the_raw_value_not_a_python_literal():
    session = VariableResolutionSession(environment=_environment(baseUrl=("api.exemplo.com", False)))

    value = session.resolve_compile_time("baseUrl")

    assert value == "api.exemplo.com"  # não '"api.exemplo.com"'


def test_resolve_compile_time_returns_none_when_nothing_resolves():
    session = VariableResolutionSession(environment=None)

    value = session.resolve_compile_time("id")

    assert value is None
    assert session.required_environment_variables == set()  # nunca defere aqui
    assert session.preamble_lines == []


def test_resolve_compile_time_never_uses_a_secret_value():
    session = VariableResolutionSession(environment=_environment(id=("42", True)))

    value = session.resolve_compile_time("id")

    assert value is None


# --- resolve_file_field: dedup + preâmbulo injetado -------------------------


def test_resolve_file_field_calls_the_preamble_builder_once_and_tracks_the_env_var():
    session = VariableResolutionSession(environment=None)
    calls = []

    def preamble_builder(field_key, local_name):
        calls.append((field_key, local_name))
        return (f"    # preamble for {local_name}\n",)

    local_name = session.resolve_file_field("avatar", preamble_builder)
    session.resolve_file_field("avatar", preamble_builder)  # segunda chamada não repete

    assert local_name == "avatar"
    assert calls == [("avatar", "avatar")]
    assert session.required_environment_variables == {"AQO_UPLOAD_AVATAR"}
    assert session.extra_imports == {"os", "pytest", "mimetypes"}


# --- mark_unresolved ---------------------------------------------------------


def test_mark_unresolved_accumulates_name_and_location():
    session = VariableResolutionSession(environment=None)

    session.mark_unresolved("userId", "path")
    session.mark_unresolved("comment", "query")

    assert [(item.name, item.location) for item in session.unresolved] == [
        ("userId", "path"),
        ("comment", "query"),
    ]
