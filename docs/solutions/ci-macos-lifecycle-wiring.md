# CI/release wiring for menubar lifecycle (manual apply)

Cursor blocked direct edits to `.github/workflows/*.yml`. Apply these hunks before tagging **v0.2.3.4**.

## `.github/workflows/ci.yml`

Insert after the `sdk:` job block and before `packaging-smoke:`:

```yaml
  menubar-lifecycle:
    needs: lint
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --frozen
      - run: uv pip install venvstacks
      - run: apps/netllm-mac/Scripts/build.sh release
        env:
          REBUILD_DONOR: force
      - run: bash scripts/test-menubar-e2e.sh
      - run: bash scripts/test-menubar-lifecycle.sh
```

## `.github/workflows/release.yml`

In `build-macos` job, after `apps/netllm-mac/Scripts/build.sh release` and **before** `bash packaging/scripts/create-dmg.sh`:

```yaml
      - run: bash scripts/test-menubar-e2e.sh
      - run: bash scripts/test-menubar-lifecycle.sh
```

Verify: push branch, confirm `menubar-lifecycle` job green on GitHub Actions before release tag.
