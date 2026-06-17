"""Emit a config document as qilab-dialect YAML.

qilab uses a hand-rolled indentation parser, NOT a full YAML reader. The dialect
it accepts is a strict subset:
  * 2 spaces per indent level, no tabs;
  * block mappings (``key: value``) and block sequences only — no flow style;
  * list items start with ``- `` indented 2 spaces under their key, first field
    inline after the dash;
  * comments are full-line ``#`` only — a trailing ``# ...`` after a value is
    folded *into* the value by qilab's parser, so we never emit inline comments.

pyyaml's default dump violates the sequence-indentation rule (dash at the parent
column), so we emit by hand. The emitter is generic over the document structure
(it does not hard-code field names), which keeps "add a model = edit only the
JSON Schema" true end to end.
"""

from __future__ import annotations

from typing import Any, Dict, List

INDENT = 2

# Preferred key order when emitting. Keys not listed keep their existing order
# and sort after the listed ones. Ranks are global but unambiguous because no
# two contexts share a key with a conflicting desired position.
_KEY_ORDER = {
    key: rank
    for rank, key in enumerate(
        [
            "entanglement_noise_model",
            "nodes",
            "type",
            "name",
            "node_type",
            "quantum_operations_delay_ns",
            "qubit_arrays",
            "num_qubits",
            "noise_model",
            "t1_value",
            "t2_value",
            "gate_error_rate",
            "error_rate",
            "gate_time_ns",
            "t1_ns",
            "t2_ns",
            "flip_probability",
            "single_gate_rotation",
            "two_qubit_gate_rotation",
            "measurement",
            "entanglement",
            "qubit_move",
            "qubit_swap",
            "qubit_init",
        ]
    )
}
_UNRANKED = len(_KEY_ORDER)


def _ordered_keys(mapping: Dict[str, Any]) -> List[str]:
    keys = list(mapping.keys())
    return sorted(keys, key=lambda k: (_KEY_ORDER.get(k, _UNRANKED), keys.index(k)))


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, float):
        # repr is round-trippable; qilab parses both "1000" and "0.001" / "1e-05".
        return repr(value)
    return str(value)


def _emit_mapping(mapping: Dict[str, Any], indent: int, lines: List[str]) -> None:
    pad = " " * indent
    for key in _ordered_keys(mapping):
        value = mapping[key]
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            _emit_mapping(value, indent + INDENT, lines)
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            _emit_sequence(value, indent + INDENT, lines)
        else:
            lines.append(f"{pad}{key}: {_scalar(value)}")


def _emit_sequence(items: List[Any], indent: int, lines: List[str]) -> None:
    pad = " " * indent
    for item in items:
        if isinstance(item, dict):
            _emit_mapping_item(item, indent, lines)
        elif isinstance(item, list):
            lines.append(f"{pad}-")
            _emit_sequence(item, indent + INDENT, lines)
        else:
            lines.append(f"{pad}- {_scalar(item)}")


def _emit_mapping_item(mapping: Dict[str, Any], indent: int, lines: List[str]) -> None:
    """Emit a mapping as a list item: first key inline after ``- ``."""
    pad = " " * indent
    content_indent = indent + INDENT  # column where the inline first key starts
    keys = _ordered_keys(mapping)
    if not keys:
        lines.append(f"{pad}-")
        return
    for position, key in enumerate(keys):
        value = mapping[key]
        prefix = f"{pad}- " if position == 0 else " " * content_indent
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            _emit_mapping(value, content_indent + INDENT, lines)
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            _emit_sequence(value, content_indent + INDENT, lines)
        else:
            lines.append(f"{prefix}{key}: {_scalar(value)}")


def emit_yaml(doc: Dict[str, Any]) -> str:
    """Render ``doc`` (a mapping) as qilab-dialect YAML text."""
    if not isinstance(doc, dict):
        raise TypeError("config document must be a mapping")
    lines: List[str] = []
    _emit_mapping(doc, 0, lines)
    return "\n".join(lines) + "\n"
