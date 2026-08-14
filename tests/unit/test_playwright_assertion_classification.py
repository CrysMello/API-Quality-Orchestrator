"""Parte 23 do plano de ação Playwright (Bloco 4 — Asserções Inteligentes):
classificação EXACT/DERIVED/BROAD de toda expectativa realmente gerada
(nunca de uma que não chegou a existir) — reaproveitando o AssertionPrecision
já existente desde a Parte 03 (nenhum modelo duplicado). Cobre a
propagação até GeneratedEndpointTest, a docstring do cenário e o
generation-manifest.json (Parte 15).
"""

import ast
import json

from api_quality_agent.domain.models import (
    AssertionDefinition,
    AssertionType,
    ExecutionContext,
    ExecutionMode,
    TestStrategy,
)
from api_quality_agent.domain.services import ApiAnalysisEngine
from api_quality_agent.generators.playwright import (
    AssertionPrecision,
    BROAD_STATUS_ASSERTION,
    DefaultPlaywrightTestSuiteBuilder,
    PlaywrightEndpointTestGenerator,
)
from api_quality_agent.parsers import PostmanCollectionParser

_GET_USERS = {"request": {"method": "GET", "url": "https://api.exemplo.com/users"}}


def _status_assertion(status_code: int = 200, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.STATUS_CODE,
        description=f"Status code da resposta deve ser {status_code}.",
        expected_value=status_code,
        origin=origin,
    )


def _content_type_assertion(content_type: str = "application/json") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.CONTENT_TYPE,
        description=f"Content-Type da resposta deve conter '{content_type}'.",
        expected_value=content_type,
        origin="contract",
    )


def _valid_json_body_assertion() -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.VALID_JSON_BODY,
        description="O corpo da resposta deve ser um JSON válido.",
        expected_value=None,
        origin="contract",
    )


def _schema_assertion(schema: dict, origin: str = "contract") -> AssertionDefinition:
    return AssertionDefinition(
        assertion_type=AssertionType.SCHEMA,
        description="O corpo da resposta deve validar contra o schema esperado.",
        expected_value=schema,
        origin=origin,
    )


def _analyzed(request: dict):
    document = PostmanCollectionParser().parse_text(
        json.dumps(
            {
                "info": {
                    "name": "Collection",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                    ),
                },
                "item": [{"name": "R1", "id": "r1", **request}],
            }
        )
    )
    analyzed = ApiAnalysisEngine().analyze_collection_requests(document)[0]
    return analyzed.analysis, analyzed.normalized_request


def _generate(request: dict, assertions: tuple[AssertionDefinition, ...]):
    analysis, normalized_request = _analyzed(request)
    strategy = TestStrategy(
        endpoint_source=analysis.source,
        assertions=assertions,
        variable_extractions=(),
        negative_cases=(),
        warnings=(),
    )
    return PlaywrightEndpointTestGenerator().generate_endpoint(strategy, normalized_request)


def _classification(generated, assertion_name: str):
    return next(c for c in generated.assertion_classifications if c.assertion == assertion_name)


# --- EXACT --------------------------------------------------------------


def test_status_with_documented_code_is_classified_exact():
    generated = _generate(_GET_USERS, (_status_assertion(422),))

    classification = _classification(generated, "status")
    assert classification.precision is AssertionPrecision.EXACT
    assert classification.source == "contract"
    assert "422" in classification.justification
    assert "Status: 422" in generated.content
    assert "[EXACT]" in generated.content
    ast.parse(generated.content)


def test_content_type_is_classified_exact():
    generated = _generate(
        _GET_USERS, (_status_assertion(), _content_type_assertion("application/json"))
    )

    classification = _classification(generated, "content_type")
    assert classification.precision is AssertionPrecision.EXACT
    assert "Content-Type: application/json" in generated.content
    assert generated.content.count("[EXACT]") >= 2  # status + content_type


def test_body_top_level_type_is_classified_exact():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(
        _GET_USERS, (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema))
    )

    classification = _classification(generated, "body")
    assert classification.precision is AssertionPrecision.EXACT
    assert "object" in classification.justification


def test_required_fields_and_field_types_are_classified_exact():
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }
    generated = _generate(
        _GET_USERS, (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema))
    )

    required_classification = _classification(generated, "required_fields")
    type_classification = _classification(generated, "field_types")
    assert required_classification.precision is AssertionPrecision.EXACT
    assert type_classification.precision is AssertionPrecision.EXACT


def test_json_schema_validation_is_classified_exact():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    generated = _generate(
        _GET_USERS, (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema))
    )

    classification = _classification(generated, "json_schema")
    assert classification.precision is AssertionPrecision.EXACT
    assert "jsonschema" in classification.justification


def test_const_expected_value_is_classified_exact():
    schema = {"type": "object", "properties": {"status": {"const": "active"}}}
    generated = _generate(
        _GET_USERS, (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema))
    )

    classification = _classification(generated, "expected_values")
    assert classification.precision is AssertionPrecision.EXACT


# --- DERIVED --------------------------------------------------------------


def test_multi_value_enum_is_classified_derived():
    schema = {
        "type": "object",
        "properties": {"role": {"type": "string", "enum": ["admin", "user"]}},
    }
    generated = _generate(
        _GET_USERS, (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema))
    )

    classification = next(
        c
        for c in generated.assertion_classifications
        if c.assertion == "expected_values" and c.precision is AssertionPrecision.DERIVED
    )
    assert "enum" in classification.justification
    assert "[DERIVED" in generated.content


def test_derived_never_appears_alongside_exact_for_the_same_check():
    # const (EXACT) e enum de 2+ valores (DERIVED) no mesmo endpoint geram
    # DUAS classificações separadas, nunca uma única "média".
    schema = {
        "type": "object",
        "properties": {
            "status": {"const": "active"},
            "role": {"type": "string", "enum": ["admin", "user"]},
        },
    }
    generated = _generate(
        _GET_USERS, (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema))
    )

    expected_value_classifications = [
        c for c in generated.assertion_classifications if c.assertion == "expected_values"
    ]
    precisions = {c.precision for c in expected_value_classifications}
    assert precisions == {AssertionPrecision.EXACT, AssertionPrecision.DERIVED}


# --- BROAD ----------------------------------------------------------------


def test_status_without_evidence_is_classified_broad_with_mandatory_warning():
    generated = _generate(_GET_USERS, ())

    classification = _classification(generated, "status")
    assert classification.precision is AssertionPrecision.BROAD
    assert classification.source == "none"
    # Regra 2: BROAD deve gerar obrigatoriamente BROAD_STATUS_ASSERTION.
    assert any(w.code == BROAD_STATUS_ASSERTION for w in generated.warnings)
    assert "[BROAD]" in generated.content
    ast.parse(generated.content)


def test_body_without_schema_is_classified_broad_with_equivalent_warning():
    generated = _generate(_GET_USERS, (_status_assertion(), _valid_json_body_assertion()))

    classification = _classification(generated, "body")
    assert classification.precision is AssertionPrecision.BROAD
    # "ou warning equivalente ao tipo da asserção" — BODY_STRUCTURE_NOT_DETERMINED
    # já cumpre esse papel para a categoria "body".
    assert any(w.code == "BODY_STRUCTURE_NOT_DETERMINED" for w in generated.warnings)


def test_broad_is_never_counted_as_exact_in_the_same_endpoint():
    generated = _generate(_GET_USERS, ())

    precisions = [c.precision for c in generated.assertion_classifications]
    assert AssertionPrecision.EXACT not in precisions
    assert AssertionPrecision.BROAD in precisions


# --- ausência de expectativa: nunca classificado -----------------------


def test_content_type_without_evidence_is_never_classified():
    generated = _generate(_GET_USERS, (_status_assertion(),))

    assert all(c.assertion != "content_type" for c in generated.assertion_classifications)


def test_no_assertions_at_all_still_classifies_status_as_broad_and_nothing_else():
    generated = _generate(_GET_USERS, ())

    assert [c.assertion for c in generated.assertion_classifications] == ["status"]


# --- toda expectativa tem precision/source/justification -------------------


def test_every_classification_has_precision_source_and_justification():
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "role": {"type": "string", "enum": ["admin", "user"]},
        },
        "required": ["id"],
    }
    generated = _generate(
        _GET_USERS,
        (
            _status_assertion(),
            _content_type_assertion(),
            _valid_json_body_assertion(),
            _schema_assertion(schema),
        ),
    )

    assert len(generated.assertion_classifications) >= 6
    for classification in generated.assertion_classifications:
        assert classification.precision in AssertionPrecision
        assert classification.source
        assert classification.justification


# --- propagação até o generation-manifest.json ------------------------------


def _context() -> ExecutionContext:
    return ExecutionContext.create(mode=ExecutionMode.OFFLINE, source="test", collection_name="Col")


def _manifest_payload(endpoint_tests):
    suite = DefaultPlaywrightTestSuiteBuilder().build(endpoint_tests, _context())
    manifest_file = next(f for f in suite.files if f.relative_path == "generation-manifest.json")
    return json.loads(manifest_file.content)


def test_classification_reaches_the_manifest_for_exact_derived_and_broad():
    schema = {
        "type": "object",
        "properties": {"role": {"type": "string", "enum": ["admin", "user"]}},
    }
    exact_and_derived = _generate(
        _GET_USERS, (_status_assertion(), _valid_json_body_assertion(), _schema_assertion(schema))
    )
    broad = _generate(_GET_USERS, ())

    payload = _manifest_payload([exact_and_derived, broad])

    summary = payload["assertion_classifications"]["summary"]
    assert summary["exact"] > 0
    assert summary["derived"] > 0
    assert summary["broad"] > 0

    entries = payload["assertion_classifications"]["entries"]
    precisions_present = {entry["precision"] for entry in entries}
    assert precisions_present == {"exact", "derived", "broad"}
    for entry in entries:
        assert entry["source"]
        assert entry["justification"]
        assert entry["endpoint"]
        assert entry["assertion"]


def test_manifest_summary_never_merges_broad_into_exact():
    broad = _generate(_GET_USERS, ())

    payload = _manifest_payload([broad])

    summary = payload["assertion_classifications"]["summary"]
    assert summary == {"exact": 0, "derived": 0, "broad": 1}
