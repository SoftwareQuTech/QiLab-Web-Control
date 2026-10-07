#!/usr/bin/env bash
# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

# Mirror every gate a vX.Y.Z tag has to clear in .gitlab-ci.yml, locally.
#
# Usage: .claude/skills/bump-version/check_release.sh 0.4.3
#
# Each check names the CI job it stands in for, so a failure here points at the
# job that would have failed after the tag was already pushed. Run from the
# repository root.

set -uo pipefail

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 <version>   (e.g. 0.4.3, no leading v)"
  exit 2
fi

PY=./env/bin/python
[[ -x "$PY" ]] || PY=python3

FAILURES=0
WARNINGS=0
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; WARNINGS=$((WARNINGS + 1)); }
step() { printf '\n%s\n' "$1"; }

# --- tag shape --------------------------------------------------------------
# Not a job: `workflow:` only starts a pipeline for tags matching this. A tag in
# any other shape produces NO pipeline at all -- no failure to notice, no
# release, just silence. Worth failing loudly on here.
step "workflow: rules (tag shape)"
if [[ "$TARGET" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  pass "v$TARGET matches ^v\\d+\\.\\d+\\.\\d+\$"
else
  fail "'$TARGET' is not X.Y.Z -- tag v$TARGET would start no pipeline at all"
fi

# --- verify:tag_version -----------------------------------------------------
step "verify:tag_version"
SETUP_VERSION="$("$PY" setup.py --version 2>/dev/null | tail -1 | tr -d '[:space:]')"
if [[ "$SETUP_VERSION" == "$TARGET" ]]; then
  pass "setup.py version == $TARGET"
else
  fail "setup.py version is '$SETUP_VERSION', tag would be '$TARGET'"
fi

# --- release:notes ----------------------------------------------------------
step "release:notes"
SECTION_REGEX="^##[[:space:]]*\\[?${TARGET}\\]?([[:space:]]*-.*)?\$"
if grep -Eq "$SECTION_REGEX" CHANGELOG.md; then
  pass "CHANGELOG.md has a section heading for $TARGET"
  BODY="$(awk -v section_regex="$SECTION_REGEX" '
    $0 ~ section_regex {in_section=1; next}
    in_section && $0 ~ /^##[[:space:]]+/ && $0 !~ section_regex {exit}
    in_section {print}
  ' CHANGELOG.md | tr -d '[:space:]')"
  if [[ -n "$BODY" ]]; then
    pass "the extracted section is non-empty"
  else
    fail "the $TARGET section is empty -- release:notes refuses to publish it"
  fi
  if grep -Eq '^##[[:space:]]*\[?Unreleased\]?' CHANGELOG.md; then
    warn "an [Unreleased] section still exists; fold it into $TARGET or leave it deliberately empty"
  fi
else
  fail "CHANGELOG.md has no '## [$TARGET] - <date>' section"
fi

# --- verify:vendored_schema -------------------------------------------------
step "verify:vendored_schema"
MANIFEST_VERSION="$(tr -d '[:space:]' < app/config/VENDORED_SCHEMA_VERSION)"
SCHEMA_VERSION="$("$PY" -c \
  "import json; print(json.load(open('app/config/config.schema.json')).get('x-qilab-schema-version',''))")"
if [[ "$MANIFEST_VERSION" == "$SCHEMA_VERSION" ]]; then
  pass "VENDORED_SCHEMA_VERSION == x-qilab-schema-version ($SCHEMA_VERSION)"
else
  fail "VENDORED_SCHEMA_VERSION '$MANIFEST_VERSION' != schema's '$SCHEMA_VERSION'"
fi

# --- verify:smoke -----------------------------------------------------------
step "verify:smoke"
if "$PY" -c 'import app.main' >/dev/null 2>&1; then
  pass "import app.main"
else
  fail "import app.main raised -- see: $PY -c 'import app.main'"
fi

# --- verify:tests -----------------------------------------------------------
step "verify:tests"
TEST_OUT="$("$PY" -m pytest -q 2>&1)"
TEST_SUMMARY="$(printf '%s\n' "$TEST_OUT" | tail -1)"
if printf '%s\n' "$TEST_OUT" | grep -q "^FAILED\|^ERROR"; then
  # test_vendored_matches_qilab_source compares against a local qilab checkout
  # and skips when absent, so it fails here and passes in CI. Every other
  # failure is a genuine release blocker.
  OTHER="$(printf '%s\n' "$TEST_OUT" | grep "^FAILED\|^ERROR" | grep -v test_vendored_matches_qilab_source)"
  if [[ -z "$OTHER" ]]; then
    warn "only test_vendored_matches_qilab_source failed: local-only (skips in CI), but the"
    warn "  vendored schema HAS drifted from the qilab checkout -- re-vendor it deliberately"
    pass "no CI-blocking test failures ($TEST_SUMMARY)"
  else
    fail "tests failed: $TEST_SUMMARY"
    printf '%s\n' "$OTHER" | sed 's/^/        /'
  fi
else
  pass "$TEST_SUMMARY"
fi

# --- build:dist + release:gitlab -------------------------------------------
step "build:dist / release:gitlab"
DIST_DIR="$(mktemp -d)"
trap 'rm -rf "$DIST_DIR"' EXIT
if "$PY" -m build --outdir "$DIST_DIR" >"$DIST_DIR/build.log" 2>&1; then
  pass "python -m build"
  WHEELS=("$DIST_DIR"/*.whl); SDISTS=("$DIST_DIR"/*.tar.gz)
  # release:gitlab hard-requires exactly one of each.
  if [[ ${#WHEELS[@]} -eq 1 && ${#SDISTS[@]} -eq 1 ]]; then
    pass "exactly one wheel and one sdist"
  else
    fail "expected 1 wheel + 1 sdist, got ${#WHEELS[@]} + ${#SDISTS[@]}"
  fi
  WHEEL_NAME="$(basename "${WHEELS[0]}")"
  if [[ "$WHEEL_NAME" == "qilab_web_control-$TARGET-"* ]]; then
    pass "artifact carries the target version ($WHEEL_NAME)"
  else
    fail "built $WHEEL_NAME, expected qilab_web_control-$TARGET-*"
  fi
  # The deployment assets are what make a release installable at all: an
  # installer copies self_update.sh and the unit out of the installed package.
  for asset in app/deploy/self_update.sh app/deploy/qilab-web-control.service; do
    if "$PY" -c "
import sys, zipfile
sys.exit(0 if '$asset' in zipfile.ZipFile('${WHEELS[0]}').namelist() else 1)"; then
      pass "wheel ships $asset"
    else
      fail "wheel is missing $asset -- check package_data in setup.py"
    fi
  done
else
  fail "python -m build failed:"
  tail -5 "$DIST_DIR/build.log" | sed 's/^/        /'
fi

# --- repository state -------------------------------------------------------
step "repository state"
if [[ -z "$(git status --porcelain)" ]]; then
  pass "working tree clean"
else
  warn "uncommitted changes -- the tag must land on a commit that has the bumped setup.py"
fi
if git rev-parse -q --verify "refs/tags/v$TARGET" >/dev/null; then
  fail "tag v$TARGET already exists locally"
else
  pass "tag v$TARGET is free"
fi

# --- stale version literals (not a CI gate) ---------------------------------
step "version literals in docs (not CI-gated, but they drift)"
STALE="$(grep -rnE '[0-9]+\.[0-9]+\.[0-9]+' README.md docs/*.md app/deploy/self_update.sh 2>/dev/null \
  | sed -E 's/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+//g' \
  | grep -E '[0-9]+\.[0-9]+\.[0-9]+' \
  | grep -vE "(^|[^0-9.])${TARGET}([^0-9.]|\$)" \
  | cut -c1-108 || true)"
if [[ -z "$STALE" ]]; then
  pass "no stale version examples"
else
  warn "examples still naming an older version:"
  printf '%s\n' "$STALE" | sed 's/^/        /'
  warn "  (tests/ fixtures are deliberately excluded -- leave those alone)"
fi

step "$([[ $FAILURES -eq 0 ]] && echo "READY for v$TARGET" || echo "NOT READY for v$TARGET")"
echo "  $FAILURES blocking, $WARNINGS advisory"
[[ $FAILURES -eq 0 ]]
