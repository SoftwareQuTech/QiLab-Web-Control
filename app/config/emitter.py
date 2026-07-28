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

from typing import Any, Dict, List, Optional

INDENT = 2


def key_order(schema: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Rank property names by where they appear in a depth-first walk of ``schema``.

    Emitting in schema order keeps saved files readable and their diffs stable.
    It is derived rather than listed because a hand-maintained order silently
    goes stale on a schema rename -- the ranks are global but unambiguous, since
    a union's ``type`` is always visited before that union's own fields.
    """
    ranks: Dict[str, int] = {}
    if not isinstance(schema, dict):
        return ranks

    def resolve(node: Any) -> Dict[str, Any]:
        seen = 0
        while isinstance(node, dict) and "$ref" in node and seen < 32:
            target: Any = schema
            for part in str(node["$ref"]).lstrip("#/").split("/"):
                target = (target or {}).get(part) if isinstance(target, dict) else None
            node = target
            seen += 1
        return node if isinstance(node, dict) else {}

    def walk(node: Any) -> None:
        node = resolve(node)
        for branch in node.get("oneOf", []) or []:
            walk(branch)
        for key, child in (node.get("properties", {}) or {}).items():
            ranks.setdefault(key, len(ranks))
            walk(child)
        if "items" in node:
            walk(node["items"])

    walk(schema)
    return ranks


def _ordered_keys(mapping: Dict[str, Any], order: Dict[str, int]) -> List[str]:
    """Schema order first; anything unranked keeps its insertion order, after."""
    keys = list(mapping.keys())
    unranked = len(order)
    return sorted(keys, key=lambda k: (order.get(k, unranked), keys.index(k)))


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, float):
        # repr is round-trippable; qilab parses both "1000" and "0.001" / "1e-05".
        return repr(value)
    return str(value)


def _emit_mapping(mapping: Dict[str, Any], indent: int, lines: List[str], order: Dict[str, int]) -> None:
    pad = " " * indent
    for key in _ordered_keys(mapping, order):
        value = mapping[key]
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            _emit_mapping(value, indent + INDENT, lines, order)
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            _emit_sequence(value, indent + INDENT, lines, order)
        else:
            lines.append(f"{pad}{key}: {_scalar(value)}")


def _emit_sequence(items: List[Any], indent: int, lines: List[str], order: Dict[str, int]) -> None:
    pad = " " * indent
    for item in items:
        if isinstance(item, dict):
            _emit_mapping_item(item, indent, lines, order)
        elif isinstance(item, list):
            lines.append(f"{pad}-")
            _emit_sequence(item, indent + INDENT, lines, order)
        else:
            lines.append(f"{pad}- {_scalar(item)}")


def _emit_mapping_item(mapping: Dict[str, Any], indent: int, lines: List[str], order: Dict[str, int]) -> None:
    """Emit a mapping as a list item: first key inline after ``- ``."""
    pad = " " * indent
    content_indent = indent + INDENT  # column where the inline first key starts
    keys = _ordered_keys(mapping, order)
    if not keys:
        lines.append(f"{pad}-")
        return
    for position, key in enumerate(keys):
        value = mapping[key]
        prefix = f"{pad}- " if position == 0 else " " * content_indent
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            _emit_mapping(value, content_indent + INDENT, lines, order)
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            _emit_sequence(value, content_indent + INDENT, lines, order)
        else:
            lines.append(f"{prefix}{key}: {_scalar(value)}")


def emit_yaml(doc: Dict[str, Any], order: Optional[Dict[str, int]] = None) -> str:
    """Render ``doc`` (a mapping) as qilab-dialect YAML text.

    ``order`` is a ``key_order(schema)`` rank map; without it, keys keep the order
    they already have -- which is schema order anyway for documents that came from
    the editor, since it serializes by walking the schema.
    """
    if not isinstance(doc, dict):
        raise TypeError("config document must be a mapping")
    lines: List[str] = []
    _emit_mapping(doc, 0, lines, order or {})
    return "\n".join(lines) + "\n"
