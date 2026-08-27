# Releasing AMP

Releases are deliberate maintainer actions. A new Minecraft release may create
a compatibility candidate, but it never publishes AMP automatically. See
[Versioning](VERSIONING.md).

Before the first PyPI release, register a pending Trusted Publisher for project
`amp-mc` with owner `AveriWylie`, repository `AMP`, workflow
`release.yml`, and environment `pypi`. No PyPI token is stored in GitHub.

## 1. Automated checks

From a clean checkout, install the development dependencies and run:

```bash
python -m pip install --require-hashes -r requirements-lock.txt
python -m pip install --no-deps .
python -m pytest
python tools/sync_minecraft_data.py --check
python tools/check_version_data.py
python -m build --no-isolation
```

Confirm that CI passes on every configured Python version. Review outstanding
security and dependency-audit findings before continuing.

## 2. Human testing

Complete every item against a disposable local world and supported Minecraft
version:

- Send a real Anthropic request in guided mode and confirm its action succeeds.
- Send a real OpenAI-compatible request in guided mode and confirm its action
  succeeds.
- Complete 1 autonomous goal with either provider, including at least 1
  successful action and server-confirmed feedback.
- Confirm that invalid or missing provider configuration fails before Java
  starts and provides an actionable message.
- Stop the local-world workflow and answer No to copy-back. Confirm that the
  source world remains unchanged.
- Stop the local-world workflow and answer Yes to copy-back. Confirm that AMP
  creates a timestamped backup and replaces the source with the modified world.
- Restart the same source world and Minecraft version. Confirm that AMP reuses
  the existing active profile.
- Start a second source world on the same Minecraft version. Confirm that AMP
  creates and uses a different active profile.
- Perform a final gameplay smoke test: connect, move, mine, place, attack, and
  stop cleanly.

Use [Testing](TESTING.md) for the live gameplay commands and [Local-world
usage](USAGE.md) for startup, shutdown, and copy-back behavior.

## 3. Release preparation

1. Confirm that the README compatibility table matches
   `amp/protocol/version_support.json`.
2. Confirm that every advertised Minecraft version has offline and live
   verification evidence.
3. Add the release date to the matching entry in `docs/CHANGELOG.md`.
4. Confirm that the project license and third-party notices are present.
5. Install the wheel in a clean environment and smoke-test both `amp --help`
   and `amp-world --help`.
6. Confirm that `requirements-lock.txt` matches the validated release and test
   environment.
7. Review the complete release diff and confirm that the worktree is clean.

## 4. Publication

1. Create an annotated `vMAJOR.MINOR.PATCH` tag at the verified commit.
2. Push the commit and tag. The release workflow verifies that the tag matches
   `pyproject.toml`, runs the offline suite, and builds once. It publishes to
   PyPI through Trusted Publishing and then attaches the same artifacts to the
   GitHub release, so a failed PyPI upload leaves no published release behind.
3. Confirm that both published artifacts contain the license and Minecraft
   runtime data. Confirm that the source distribution also contains the project
   documents and protocol fixtures.
