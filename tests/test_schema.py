# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Schema rules + the drift guard: qilab's schema must match the vendored copy
and validate qilab's known-good configs."""

import copy
import json
import os
from pathlib import Path

import jsonschema
import pytest
import yaml

from app.config import schema as schema_module

# qilab's source-of-truth schema, if a qilab checkout sits next to this one
# (override with QILAB_REPO_SCHEMA).
QILAB_SCHEMA = Path(
    os.getenv(
        "QILAB_REPO_SCHEMA",
        str(Path(__file__).resolve().parents[2] / "qilab" / "docs" / "config.schema.json"),
    )
)

# What qilab's create_default_config() writes (config_loader.cpp).
QILAB_DEFAULT_CONFIG = """
nodes:
  - name: node0
    node_type: GENERIC
    two_qubit_gate_noise_model:
      type: DEPOLARIZING
      depolarizing_probability: 0.0
    quantum_operations_delay_ns:
      two_qubit_gate_rotation: 0
      measurement: 0
      entanglement: 0
      qubit_move: 0
      qubit_swap: 0
      qubit_init: 0
    qubit_arrays:
      - num_qubits: 10
        single_gate_delay_ns: 0
        qubit_decoherence_noise_model:
          type: BASIC
          t2_value: 1000
        gate_noise_model:
          type: DEPOLARIZING
          depolarizing_probability: 0.0
"""


def load_schema():
    schema, _, error = schema_module.get_schema(force=True)
    assert schema is not None, error
    return schema


def test_schema_is_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(load_schema())


def test_vendored_matches_qilab_source():
    if not QILAB_SCHEMA.exists():
        pytest.skip("qilab repo not present in this checkout")
    vendored = schema_module.VENDORED_SCHEMA.read_text(encoding="utf-8")
    assert vendored == QILAB_SCHEMA.read_text(encoding="utf-8"), (
        "Vendored config.schema.json has drifted from qilab's docs/config.schema.json"
    )


def test_accepts_qilab_default_config():
    doc = yaml.safe_load(QILAB_DEFAULT_CONFIG)
    assert schema_module.validate(doc, load_schema()) == []


def test_accepts_all_noise_models():
    schema = load_schema()
    models = {
        "BASIC": {"type": "BASIC", "t2_value": 1000},
        "ADVANCED": {"type": "ADVANCED", "t1_value": 5000, "t2_value": 2000, "gate_error_rate": 0.001},
        "PHASE_FLIP": {"type": "PHASE_FLIP", "t2_value": 1000},
    }
    for model in models.values():
        doc = {"nodes": [{"qubit_arrays": [qubit_array(decoherence=model)]}]}
        assert schema_module.validate(doc, schema) == [], model


def qubit_array(**overrides):
    """A minimal valid qubit array; `decoherence=` swaps the decoherence model."""
    array = {
        "num_qubits": 1,
        "single_gate_delay_ns": 0,
        "qubit_decoherence_noise_model": {"type": "BASIC", "t2_value": 1000},
        "gate_noise_model": {"type": "DEPOLARIZING", "depolarizing_probability": 0.0},
    }
    if "decoherence" in overrides:
        array["qubit_decoherence_noise_model"] = overrides.pop("decoherence")
    array.update(overrides)
    return array


def base_doc():
    return {"nodes": [{"qubit_arrays": [qubit_array()]}]}


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.pop("nodes"), id="missing-nodes"),
        pytest.param(lambda d: d.__setitem__("nodes", []), id="empty-nodes"),
        pytest.param(
            lambda d: d["nodes"][0]["qubit_arrays"][0].update(num_qubits=0), id="zero-num-qubits"
        ),
        pytest.param(
            lambda d: d["nodes"][0]["qubit_arrays"][0].__setitem__("qubit_decoherence_noise_model", {"type": "BASIC", "t2_value": -1}),
            id="negative-float",
        ),
        pytest.param(
            lambda d: d["nodes"][0].__setitem__("node_type", "BOGUS"), id="bad-enum"
        ),
        pytest.param(
            lambda d: d["nodes"][0]["qubit_arrays"][0]["qubit_decoherence_noise_model"].update(extra=1), id="unknown-key"
        ),
        pytest.param(
            lambda d: d["nodes"][0]["qubit_arrays"][0].__setitem__("qubit_decoherence_noise_model", {"type": "ADVANCED", "t2_value": 1}),
            id="missing-required-field",
        ),
        pytest.param(
            lambda d: d["nodes"][0]["qubit_arrays"][0].__setitem__("qubit_decoherence_noise_model", {"type": "NOPE", "t2_value": 1}),
            id="unknown-model-type",
        ),
    ],
)
def test_rejects_invalid(mutate):
    schema = load_schema()
    doc = base_doc()
    mutate(doc)
    assert schema_module.validate(doc, schema), "expected validation errors"


def test_friendly_message_for_missing_model_field():
    schema = load_schema()
    doc = base_doc()
    doc["nodes"][0]["qubit_arrays"][0]["qubit_decoherence_noise_model"] = {"type": "ADVANCED", "t2_value": 1}
    errors = schema_module.validate(doc, schema)
    joined = " ".join(message for _, message in errors)
    assert "ADVANCED" in joined
    # The drilled-in message should name a genuinely missing required field.
    assert "t1_value" in joined or "gate_error_rate" in joined


def test_default_document_is_valid():
    assert schema_module.validate(schema_module.default_document(), load_schema()) == []
