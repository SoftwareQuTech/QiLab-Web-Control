"""Load qilab's JSON Schema and validate config documents against it.

The schema is qilab's source of truth (``<qilab>/docs/config.schema.json``). The
control server reads a copy resolved in this order:

  1. ``QILAB_WEB_SCHEMA_PATH``  -> explicit path (e.g. the qilab repo/deploy copy)
  2. vendored fallback          -> ``app/config/config.schema.json`` (always present)

The fallback guarantees the editor still renders if the qilab copy is missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jsonschema

_PKG_DIR = Path(__file__).resolve().parent
VENDORED_SCHEMA = _PKG_DIR / "config.schema.json"

# Cached load result: (schema_or_None, source, error_or_None)
_cache: Optional[Tuple[Optional[Dict[str, Any]], str, Optional[str]]] = None


def schema_path() -> Path:
    explicit = os.getenv("QILAB_WEB_SCHEMA_PATH")
    return Path(explicit) if explicit else VENDORED_SCHEMA


def _load_from(path: Path) -> Dict[str, Any]:
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return schema


def get_schema(force: bool = False) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    """Return ``(schema, source, error)``; ``schema`` is None when unavailable."""
    global _cache
    if _cache is not None and not force:
        return _cache

    path = schema_path()
    try:
        _cache = (_load_from(path), str(path), None)
        return _cache
    except Exception as primary_exc:  # noqa: BLE001 - report, then try fallback
        if path != VENDORED_SCHEMA:
            try:
                schema = _load_from(VENDORED_SCHEMA)
                _cache = (
                    schema,
                    f"{VENDORED_SCHEMA} (fallback)",
                    f"{path} failed: {primary_exc}",
                )
                return _cache
            except Exception as fallback_exc:  # noqa: BLE001
                _cache = (None, str(path), f"{primary_exc}; fallback failed: {fallback_exc}")
                return _cache
        _cache = (None, str(path), str(primary_exc))
        return _cache


def reload_schema() -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    return get_schema(force=True)


def default_document() -> Dict[str, Any]:
    """A minimal valid config used as a blank starting point in the editor.

    Must satisfy every ``required`` key of the vendored schema -- a qubit array
    needs all four, and both noise probabilities default to 0 (noiseless) so a
    blank config is a well-defined no-noise simulation rather than an arbitrary
    one. ``test_default_document_is_valid`` pins this.
    """
    return {
        "nodes": [
            {
                "qubit_arrays": [
                    {
                        "num_qubits": 1,
                        "single_gate_delay_ns": 0,
                        "qubit_decoherence_noise_model": {"type": "BASIC", "t2_value": 1000},
                        "gate_noise_model": {
                            "type": "DEPOLARIZING",
                            "depolarizing_probability": 0,
                        },
                    }
                ]
            }
        ]
    }


def _path_str(error: "jsonschema.exceptions.ValidationError") -> str:
    rendered = ""
    for part in error.absolute_path:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else str(part)
    return rendered or "(root)"


def _branch_types(branches: List[Dict[str, Any]]) -> List[str]:
    types = []
    for branch in branches:
        const = branch.get("properties", {}).get("type", {}).get("const")
        if const:
            types.append(const)
    return types


def _describe(error: "jsonschema.exceptions.ValidationError") -> str:
    """Produce a readable message, drilling into discriminated-union failures."""
    if error.validator == "oneOf":
        instance = error.instance
        branches = error.validator_value or []
        if isinstance(instance, dict) and "type" in instance:
            wanted = instance["type"]
            for branch in branches:
                if branch.get("properties", {}).get("type", {}).get("const") == wanted:
                    sub_errors = list(jsonschema.Draft202012Validator(branch).iter_errors(instance))
                    if sub_errors:
                        return f"invalid '{wanted}' model: " + "; ".join(e.message for e in sub_errors)
                    break
            return f"unknown type '{wanted}'; expected one of {_branch_types(branches)}"
        return f"must set 'type' to one of {_branch_types(branches)}"
    return error.message


def validate(doc: Any, schema: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
    """Validate ``doc`` and return a list of ``(path, message)`` errors (empty = valid)."""
    if schema is None:
        schema, _, _ = get_schema()
    if schema is None:
        return [("(schema)", "Config schema is unavailable; cannot validate.")]

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    return [(_path_str(error), _describe(error)) for error in errors]
