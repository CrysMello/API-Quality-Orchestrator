"""Parte 12 do plano de ação Playwright: NormalizedAuthParameter.__repr__
nunca expõe um valor literal (possível segredo hardcoded na Collection) —
só uma referência pura a variável Postman é considerada segura de exibir.
"""

from api_quality_agent.domain.models import NormalizedAuthParameter


def test_repr_masks_a_literal_value():
    parameter = NormalizedAuthParameter(key="token", value="super-secret-literal-123")

    assert "super-secret-literal-123" not in repr(parameter)
    assert "super-secret-literal-123" not in str(parameter)
    assert "token" in repr(parameter)


def test_repr_shows_a_pure_variable_reference():
    parameter = NormalizedAuthParameter(key="token", value="{{accessToken}}")

    assert "{{accessToken}}" in repr(parameter)


def test_repr_masks_a_value_that_only_partially_is_a_variable_reference():
    parameter = NormalizedAuthParameter(key="token", value="Bearer {{accessToken}}")

    assert "Bearer {{accessToken}}" not in repr(parameter)
    assert "{{accessToken}}" not in repr(parameter) or "***" in repr(parameter)


def test_repr_handles_none_value():
    parameter = NormalizedAuthParameter(key="token", value=None)

    assert repr(parameter) == "NormalizedAuthParameter(key='token', value=None)"


def test_str_uses_the_same_masking_as_repr():
    parameter = NormalizedAuthParameter(key="password", value="hunter2")

    assert str(parameter) == repr(parameter)
    assert "hunter2" not in str(parameter)
