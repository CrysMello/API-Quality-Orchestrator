"""Parte 05 do plano de ação Playwright: nomeação determinística dos
arquivos por endpoint (test_<metodo>_<path>.py) e resolução de colisões.
"""

from api_quality_agent.generators.playwright import (
    FILE_NAME_COLLISION_RESOLVED,
    endpoint_source_to_file_name,
    resolve_endpoint_file_names,
)

# --- Exemplos literais do plano de ação --------------------------------------


def test_simple_get_endpoint():
    assert endpoint_source_to_file_name("GET /users") == "test_get_users.py"


def test_path_parameter_with_braces_becomes_by_prefix():
    assert endpoint_source_to_file_name("GET /users/{id}") == "test_get_users_by_id.py"


def test_camel_case_path_parameter_is_converted_to_snake_case():
    assert (
        endpoint_source_to_file_name("POST /orders/{orderId}/items")
        == "test_post_orders_by_order_id_items.py"
    )


# --- Determinismo -------------------------------------------------------------


def test_same_endpoint_always_produces_the_same_name():
    results = {endpoint_source_to_file_name("GET /pets/{petId}") for _ in range(20)}
    assert results == {"test_get_pets_by_pet_id.py"}


def test_determinism_across_all_endpoints_in_a_batch():
    endpoints = ["GET /users", "POST /users", "GET /users/{id}"]
    first_run = resolve_endpoint_file_names(endpoints).file_names
    second_run = resolve_endpoint_file_names(endpoints).file_names
    assert first_run == second_run


# --- Parâmetros de path em formatos diferentes (Postman ":id" / OpenAPI "{id}") ---


def test_postman_style_colon_parameter_is_recognized():
    assert endpoint_source_to_file_name("DELETE /users/:id") == "test_delete_users_by_id.py"


def test_double_brace_parameter_is_recognized():
    assert endpoint_source_to_file_name("GET /pets/{{petId}}") == "test_get_pets_by_pet_id.py"


# --- Sanitização geral ----------------------------------------------------------


def test_lowercase_and_underscore_separator_are_enforced():
    name = endpoint_source_to_file_name("Get /Users/Active-Pets")
    assert name == name.lower()
    assert "-" not in name


def test_invalid_characters_are_removed():
    name = endpoint_source_to_file_name("GET /users/profile.picture")
    assert name == "test_get_users_profile_picture.py"


# --- Nomes longos: truncamento seguro com hash (Windows MAX_PATH) --------------


def test_long_path_is_truncated_with_a_deterministic_hash_suffix():
    long_path = "/".join(f"resource{n}" for n in range(20))  # bem além de 40 chars
    endpoint_source = f"GET /{long_path}"

    name = endpoint_source_to_file_name(endpoint_source)

    # "test_" (5) + slug (<=40) + ".py" (3): nunca deve crescer sem limite.
    assert len(name) <= 5 + 40 + 3
    # Mesmo endpoint longo continua determinístico após o truncamento.
    assert name == endpoint_source_to_file_name(endpoint_source)


def test_two_different_long_paths_truncated_to_the_same_prefix_do_not_collide():
    # Dois paths que só diferem depois do ponto de corte de 40 caracteres
    # produziriam o mesmo nome se o truncamento não incluísse um hash do
    # conteúdo completo — o hash garante que continuam distintos.
    base = "a" * 60
    endpoint_a = f"GET /{base}xxxx"
    endpoint_b = f"GET /{base}yyyy"

    assert endpoint_source_to_file_name(endpoint_a) != endpoint_source_to_file_name(endpoint_b)


def test_resulting_file_name_never_exceeds_windows_safe_length_even_with_collision_suffix():
    long_path = "/".join(f"segment{n}" for n in range(15))
    endpoints = [f"GET /{long_path}", f"GET /{long_path}"]  # força colisão

    resolved = resolve_endpoint_file_names(endpoints)

    for name in resolved.file_names:
        assert len(name) <= 5 + 40 + len("_99") + 3


# --- Colisões: sufixo determinístico, sem sobrescrever, com warning -----------


def test_colliding_endpoints_get_distinct_file_names():
    # Dois endpoints diferentes cujo path sanitizado colide (parâmetros com
    # nomes diferentes, mesmo slug final por coincidência de escrita).
    endpoints = ["GET /users/{id}", "GET /users/:id"]

    resolved = resolve_endpoint_file_names(endpoints)

    assert len(set(resolved.file_names)) == len(resolved.file_names)
    assert resolved.file_names == ("test_get_users_by_id.py", "test_get_users_by_id_02.py")


def test_collision_generates_file_name_collision_resolved_warning():
    endpoints = ["GET /users", "GET /users"]

    resolved = resolve_endpoint_file_names(endpoints)

    assert len(resolved.warnings) == 1
    warning = resolved.warnings[0]
    assert warning.code == FILE_NAME_COLLISION_RESOLVED
    assert warning.endpoint == "GET /users"
    assert warning.scenario is None


def test_no_collision_generates_no_warning():
    endpoints = ["GET /users", "POST /users", "DELETE /users/{id}"]

    resolved = resolve_endpoint_file_names(endpoints)

    assert resolved.warnings == ()
    assert len(set(resolved.file_names)) == 3


def test_three_way_collision_gets_incrementing_suffixes():
    endpoints = ["GET /users", "GET /users", "GET /users"]

    resolved = resolve_endpoint_file_names(endpoints)

    assert resolved.file_names == (
        "test_get_users.py",
        "test_get_users_02.py",
        "test_get_users_03.py",
    )
    assert len(resolved.warnings) == 2


# --- Robustez -------------------------------------------------------------------


def test_endpoint_without_path_does_not_crash():
    assert endpoint_source_to_file_name("GET") == "test_get.py"


def test_empty_endpoint_source_falls_back_to_the_configured_fallback():
    assert endpoint_source_to_file_name("") == "test_unknown.py"
