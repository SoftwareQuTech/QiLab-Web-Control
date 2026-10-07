# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

import yaml

from app.config import schema as config_schema
from app.config.emitter import emit_yaml, key_order

EXPECTED = """\
entanglement_noise_model:
  type: DEPOLARIZING
  error_rate: 0.05
  gate_time_ns: 200
nodes:
  - name: alice
    node_type: NV_CENTER
    qubit_arrays:
      - num_qubits: 2
        noise_model:
          type: ADVANCED
          t1_value: 5000
          t2_value: 2000
          gate_error_rate: 0.001
"""


def sample_doc():
    return {
        "entanglement_noise_model": {"type": "DEPOLARIZING", "error_rate": 0.05, "gate_time_ns": 200},
        "nodes": [
            {
                "name": "alice",
                "node_type": "NV_CENTER",
                "qubit_arrays": [
                    {
                        "num_qubits": 2,
                        "noise_model": {
                            "type": "ADVANCED",
                            "t1_value": 5000,
                            "t2_value": 2000,
                            "gate_error_rate": 0.001,
                        },
                    }
                ],
            }
        ],
    }


def test_golden_dialect_output():
    assert emit_yaml(sample_doc()) == EXPECTED


def test_two_space_indentation_and_no_tabs():
    text = emit_yaml(sample_doc())
    assert "\t" not in text
    # Block sequence items are indented under their key (qilab requires this).
    assert "\n  - name: alice" in text
    assert "\n      - num_qubits: 2" in text


def test_no_trailing_inline_comments():
    # The emitter must never produce "value # comment"; qilab folds it into the value.
    for line in emit_yaml(sample_doc()).splitlines():
        if ":" in line:
            value = line.split(":", 1)[1]
            assert "#" not in value


def test_round_trips_through_yaml():
    doc = sample_doc()
    assert yaml.safe_load(emit_yaml(doc)) == doc


def test_key_order_follows_the_schema():
    schema, _, _ = config_schema.get_schema()
    order = key_order(schema)

    # Ranks are derived, so a renamed field cannot go stale the way a hand-written
    # list did; every property of every $def gets one.
    for name in schema["properties"]:
        assert name in order
    for name in schema["$defs"]["qubit_array"]["properties"]:
        assert name in order

    # A model's discriminator must outrank the fields of every branch it selects.
    branch = schema["$defs"]["qubit_noise_model"]["oneOf"][1]["properties"]
    assert all(order["type"] < order[name] for name in branch if name != "type")


def test_key_order_puts_schema_keys_before_unknown_ones():
    schema, _, _ = config_schema.get_schema()
    doc = {"nodes": [{"legacy_extra": 1, "name": "alice"}]}

    lines = emit_yaml(doc, key_order(schema)).splitlines()

    assert lines[1].strip().startswith("- name:"), lines
    assert lines[2].strip().startswith("legacy_extra:"), lines


def test_key_order_of_none_schema_keeps_insertion_order():
    doc = {"b": 1, "a": 2}
    assert emit_yaml(doc, key_order(None)) == "b: 1\na: 2\n"


def test_emits_all_models_round_trip():
    doc = {
        "entanglement_noise_model": {"type": "DEPOLARIZING", "error_rate": 0.05, "gate_time_ns": 200},
        "nodes": [
            {
                "node_type": "GENERIC",
                "quantum_operations_delay_ns": {
                    "single_gate_rotation": 1,
                    "two_qubit_gate_rotation": 2,
                    "measurement": 3,
                    "entanglement": 4,
                    "qubit_move": 5,
                    "qubit_swap": 6,
                    "qubit_init": 7,
                },
                "qubit_arrays": [
                    {"num_qubits": 1, "noise_model": {"type": "BASIC", "t2_value": 800}},
                    {"num_qubits": 2, "noise_model": {"type": "AMPLITUDE_DAMPING", "t1_ns": 3000, "t2_ns": 1500}},
                    {"num_qubits": 3, "noise_model": {"type": "PHASE_FLIP", "flip_probability": 0.02}},
                ],
            }
        ],
    }
    assert yaml.safe_load(emit_yaml(doc)) == doc
