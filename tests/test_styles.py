# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Guard the one CSS mistake that renders a control invisible rather than ugly.

`.btn` sets `color: #fff` and leaves the background to a modifier, so a `.btn`
whose modifier is missing (or misspelled) is white text on a white card. That is
how the telemetry download links shipped invisible: nothing errors, the element
is there, it is just unreadable.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CSS = (REPO / "app/static/style.css").read_text(encoding="utf-8")
TEMPLATE = (REPO / "app/templates/index.html").read_text(encoding="utf-8")


def rule_body(selector):
    match = re.search(rf"(?:^|[,\s]){re.escape(selector)}\s*(?:,[^{{]*)?\{{([^}}]*)\}}", CSS, re.M)
    return match.group(1) if match else None


def test_bare_btn_has_its_own_background():
    body = rule_body(".btn")
    assert body is not None, "no .btn rule"
    assert "background" in body, ".btn must set a background; a bare .btn is white-on-white"


def test_anchors_styled_as_buttons_lay_out_as_buttons():
    # Several .btn elements are <a>, which without these two is an underlined
    # inline run of text that ignores the vertical padding.
    body = rule_body(".btn")
    assert "text-decoration" in body and "display" in body


def test_every_button_modifier_used_in_the_template_exists():
    used = set()
    for classes in re.findall(r'class="(btn[^"]*)"', TEMPLATE):
        used.update(name for name in classes.split() if name != "btn")

    missing = sorted(name for name in used if rule_body("." + name) is None)
    assert not missing, f"template uses undefined button modifiers: {missing}"
