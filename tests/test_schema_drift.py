# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Guard against the editor drifting from the schema again.

The structured editor and the YAML emitter are both generic over the schema *on
purpose*: they walk it at runtime instead of naming qilab's fields. When they
name fields instead, nothing fails -- the form keeps rendering, the save keeps
succeeding, and it silently shows and writes an obsolete config format after the
schema is updated. That is exactly what happened to the previous renderer, which
still asked for ``noise_model`` long after the schema had split it in two.

So the invariant is checked mechanically: no qilab field name may appear in
either file.
"""

import re
from pathlib import Path

import pytest

from app.config import schema as config_schema

REPO = Path(__file__).resolve().parents[1]

# The one exemption: ``type`` is not a field so much as JSON Schema's
# discriminator for a oneOf, and walking a union generically requires reading it.
STRUCTURAL_KEYS = {"type"}

GENERIC_SOURCES = [
    Path("app/static/config.js"),
    Path("app/config/emitter.py"),
]


def field_names():
    """Every property name anywhere in the schema."""
    schema, _, _ = config_schema.get_schema()
    names = set()

    def walk(node):
        if isinstance(node, dict):
            for key, child in (node.get("properties") or {}).items():
                names.add(key)
                walk(child)
            for key in ("items", "not"):
                if key in node:
                    walk(node[key])
            for key in ("oneOf", "anyOf", "allOf"):
                for branch in node.get(key) or []:
                    walk(branch)
            for child in (node.get("$defs") or {}).values():
                walk(child)

    walk(schema)
    return names - STRUCTURAL_KEYS


def references(text, field):
    """Is ``field`` used as a field: a string literal, or an attribute access?

    Looser matching flags prose -- "field name" is not a reference to ``name`` --
    and these two forms are how naming a field is actually written in either
    language.
    """
    escaped = re.escape(field)
    return bool(re.search(rf"""["']{escaped}["']|\.{escaped}\b""", text))


@pytest.mark.parametrize("relative", GENERIC_SOURCES, ids=lambda p: p.name)
def test_generic_sources_name_no_schema_fields(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    named = sorted(name for name in field_names() if references(text, name))
    assert not named, (
        f"{relative} mentions qilab config fields {named}; it must derive them from "
        "the schema instead, or it will keep rendering them after a schema change"
    )
