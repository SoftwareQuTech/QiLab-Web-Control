import yaml

from app.config.emitter import emit_yaml

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
