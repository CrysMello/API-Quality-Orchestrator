import pytest

from api_quality_agent.shared import mask_all_occurrences, mask_secret


def test_masks_long_value_matching_sad_example():
    assert mask_secret("P0STM4N-API-KEY-EXEMPLO-123456") == "P0ST" + "*" * 22 + "3456"


def test_masks_short_value_completely():
    assert mask_secret("abcd") == "****"


def test_masks_value_exactly_at_boundary_completely():
    assert mask_secret("abcdefgh") == "*" * 8


def test_masks_empty_string_as_empty():
    assert mask_secret("") == ""


def test_masked_value_has_same_length_as_original():
    original = "some-secret-value-1234567890"
    masked = mask_secret(original)
    assert len(masked) == len(original)


def test_masked_value_never_exposes_middle_characters():
    original = "abcdefghijklmnopqrstuvwxyz"
    masked = mask_secret(original)
    middle = original[4:-4]
    assert middle not in masked


def test_rejects_non_string_value():
    with pytest.raises(TypeError):
        mask_secret(12345)


# --- mask_all_occurrences (Gap 1 do run --target playwright) -------------------------------


def test_mask_all_occurrences_replaces_every_known_secret():
    text = "token=segredo-123456789 e outro=outro-segredo-abcdefgh"
    masked = mask_all_occurrences(text, ("segredo-123456789", "outro-segredo-abcdefgh"))

    assert "segredo-123456789" not in masked
    assert "outro-segredo-abcdefgh" not in masked
    assert "token=" in masked  # texto ao redor é preservado


def test_mask_all_occurrences_masks_every_repeated_occurrence():
    text = "primeiro: segredo-xyz, segundo: segredo-xyz"
    masked = mask_all_occurrences(text, ("segredo-xyz",))

    assert "segredo-xyz" not in masked


def test_mask_all_occurrences_never_touches_unrelated_text():
    text = "isto é um texto público, sem nada sensível"
    masked = mask_all_occurrences(text, ("valor-que-nunca-aparece",))

    assert masked == text


def test_mask_all_occurrences_with_no_secrets_is_a_no_op():
    text = "qualquer texto"

    assert mask_all_occurrences(text, ()) == text


def test_mask_all_occurrences_ignores_empty_string_in_the_list():
    # Um "" na lista nunca deveria mascarar tudo (str.replace com "" faria
    # isso) — guarda explícita contra esse caso degenerado.
    text = "texto normal"

    assert mask_all_occurrences(text, ("",)) == text
