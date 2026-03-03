Implement the approved GitLab CI/CD release pipeline in this repository.

Constraints:
- Keep CI concise.
- Release artifacts: wheel (.whl) + source tar.gz.
- Version authority: Git tag (vX.Y.Z).
- Release docs: CHANGELOG.md section for the version + generated GitLab release notes.
- Primary Debian deployment guidance: root-owned systemd service with venv.
- Do not add unrelated features.

Execute exactly in this order, stopping for review after each step.

Step 1 — Pipeline skeleton
- Create/update `.gitlab-ci.yml` with stages: `verify`, `build`, `release`.
- Add rules so branch/MR pipelines run verify jobs only.
- Add rules so tag pipelines (vX.Y.Z) run full pipeline.
Done when:
- Stage order is correct.
- Tag vs branch behavior is explicit and minimal.

Step 2 — Fast verify job
- Add `verify:smoke` job.
- Install deps needed for sanity checks.
- Run import check for `app.main`.
Done when:
- Branch pipeline catches dependency/import breakage quickly.

Step 3 — Tag/version guard
- Add `verify:tag_version` job (tag-only).
- Parse tag version (strip `v`) and compare to `setup.py` version.
Done when:
- Mismatch fails with clear error.

Step 4 — Build artifacts
- Add `build:dist` job (tag-only).
- Build wheel and sdist.
- Keep artifacts for release jobs.
Done when:
- `dist/` contains one wheel and one source tar.gz for the tag version.

Step 5 — Release notes extraction
- Ensure `CHANGELOG.md` exists; create concise initial structure if missing.
- Add `release:notes` job (tag-only) to extract notes for the tag version.
- Output `release-notes.md` as artifact.
Done when:
- Missing changelog section fails clearly.

Step 6 — Publish GitLab release
- Add `release:gitlab` job (tag-only).
- Create release for tag.
- Attach wheel + sdist artifacts.
- Use generated release notes.
Done when:
- Release page includes assets and notes.

Step 7 — README release workflow
- Update `README.md` with concise maintainer flow:
  1) update changelog
  2) ensure `setup.py` version matches tag
  3) push tag to publish release
Done when:
- Release process is clear and repeatable.

Step 8 — Debian deployment section
- Update `README.md` with wheel-based Debian run/install instructions using uvicorn.
- Add exactly 3 deployment scenarios:
  1) Root systemd + venv (primary)
  2) Dedicated non-root service user + constrained privilege helper
  3) Nginx reverse proxy in front of uvicorn + systemd
Done when:
- The three scenarios are concise and actionable.

Validation checklist
- Branch pipeline: verify jobs run; build/release do not.
- Tag pipeline: version guard, build, notes, release all run.
- Negative test: tag/version mismatch fails as expected.
- Debian sanity: wheel install in venv and uvicorn start command documented.
