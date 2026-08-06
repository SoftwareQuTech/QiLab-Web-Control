---
name: bump-version
description: Prepare a qilab_web_control release for a given version — bump setup.py, close the CHANGELOG section, refresh version examples in the docs, and verify every CI gate a vX.Y.Z tag has to clear before it is pushed. Use whenever the user names a version to release ("bump to 0.4.3", "release 0.5.0", "update the version").
---

# Releasing a version of qilab_web_control

The user names a target version. Get the repository into a state where pushing
`v<version>` publishes a release, and **prove it** by running the gates locally
rather than discovering a failure after the tag exists.

Tags are hard to retract once pushed — a GitLab release may already be created.
So: prepare, verify, report, and **only push the tag after the user explicitly
says to**.

## 1. Establish the target

Version is `X.Y.Z`, no `v` prefix in files, `v` prefix on the tag only.

If the user did not say which version, ask; do not infer a bump level from the
changes. If they name a version that is already tagged, stop and say so.

## 2. Edit what a release needs

**`setup.py`** — `version="<target>"`. This is the single source of truth;
`verify:tag_version` compares `python setup.py --version` against the tag.

**`CHANGELOG.md`** — the section heading must match what `release:notes` greps
for: `^##\s*\[?<version>\]?(\s*-.*)?$`. Use the established form:

```markdown
## [0.4.3] - 2026-08-06
```

Use the real current date (`date -u +%F`), not a guess. Then:

- If an `## [Unreleased]` section holds this release's notes, rename it to the
  version heading. Do not leave the content duplicated under both.
- Otherwise write the section from the actual commits since the last tag
  (`git log --oneline <last-tag>..HEAD`), grouped `### Fixed` / `### Added` /
  `### Changed`, matching the voice of the existing entries: what broke or
  changed, and *why it mattered* — these entries become the published release
  notes verbatim.
- The section must not be empty. `release:notes` fails on an empty extraction.

**Version examples in docs** (`README.md`, `docs/image-layer.md`,
`app/deploy/self_update.sh` usage comments) — refresh to the target so a reader
copy-pasting an install command gets the version they just released. Not
CI-gated; still worth doing.

Leave alone:
- `CHANGELOG.md` history — older sections are the record.
- version strings in `tests/` — fixture data (`qilab_web_control-0.4.1-...whl`
  in `test_self_update.py` is an arbitrary sample name, not the project version).

## 3. Verify

```bash
.claude/skills/bump-version/check_release.sh <version>
```

It mirrors every CI job for a tag pipeline and names the job each check stands
in for: tag shape (`workflow:`), `verify:tag_version`, `release:notes`,
`verify:vendored_schema`, `verify:smoke`, `verify:tests`, `build:dist` and
`release:gitlab`'s "exactly one wheel and one sdist" requirement — plus that the
built wheel actually ships `app/deploy/self_update.sh` and the unit file, since
an installer copies those out of the installed package.

Blocking failures must be fixed. Two advisory results have known meanings:

- **`test_vendored_matches_qilab_source` failed** — it compares the vendored
  schema against a local qilab checkout and *skips* when that checkout is
  absent, so it fails here and passes in CI. Not a release blocker, but it does
  mean `app/config/config.schema.json` has drifted from the local qilab. Decide
  deliberately: re-vendor it (and bump `app/config/VENDORED_SCHEMA_VERSION` to
  the new `x-qilab-schema-version`, which `verify:vendored_schema` enforces), or
  release as-is because this version deliberately targets the older schema.
- **Uncommitted changes** — fine mid-preparation, but the tag has to land on a
  commit that already contains the bumped `setup.py`, or `verify:tag_version`
  fails.

## 4. Report, then wait

Tell the user what changed, the check results, and the exact commands to
release:

```bash
git commit -am "Release <version>"
git push
git tag v<version>
git push origin v<version>
```

Push the tag only on an explicit go-ahead. If asked to do the whole thing, still
run the checks first and stop if any gate fails.

## Notes that catch people out

- A tag not matching `^v\d+\.\d+\.\d+$` starts **no pipeline at all** — no
  failure, no release, just silence. `v0.5` and `0.5.0` both do this.
- `verify:tag_version`, `build:dist` and both release jobs are tag-only; pushing
  the branch first exercises only the verify jobs. A green branch pipeline says
  nothing about whether the tag will release.
- `release:gitlab` publishes links to `build:dist`'s artifacts, which
  `expire_in: 7 days`. The release keeps working, but those asset links stop
  resolving after a week — expected, not a bug to chase.
- The wheel carries `self_update.sh`, so each release replaces the script on
  devices that self-update *to* it. A fix to the update mechanism itself cannot
  reach a device through the mechanism it fixes: that first hop needs a manual
  install or a reflash.
