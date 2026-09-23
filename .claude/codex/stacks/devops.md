### For the tester

- **Gate (run what exists on this machine):** `docker build` of each changed Dockerfile to a
  throwaway local tag, `docker compose config` for each changed compose file, `actionlint` for each
  changed workflow, `shellcheck` for each changed script. Check `which actionlint` / `which
  shellcheck` first and **name an absent tool in the report — never skip a step silently**.
- Build to a **local tag only**. Never `docker push`, never a registry login, never `docker compose
  up` against anything but a throwaway local stack, and never dispatch a workflow run: a workflow is
  verified by `actionlint` plus reading it, not by running it.
- Shell scripts: `bats` where the repo already has it, otherwise unit-test the script's pure
  functions by sourcing them. A test that needs the Docker daemon, a running container, or the
  network is `tests/integration/`, never `tests/unit/`.
- A unit test here may not reach the daemon, the network, or a real cloud API. Compose and image
  behaviour is integration tier by definition.

### For the reviewer

Real smells, worth a finding:

- **A secret in a build arg or `ENV`** — `ARG TOKEN` / `--build-arg`, a key baked into a layer, a
  `.env` copied into the image. Build-time secrets go through a secret mount; runtime ones through
  the environment. Image history keeps everything else forever.
- **`latest` or an unpinned reference** — `FROM node:latest`, an action pinned to a moving branch,
  a base image with no tag. Pin to the tag or digest form the repo already uses.
- **`tests/` or dev dependencies baked into the shipped stage** — the artifact excludes them through
  `.dockerignore` or a multi-stage build, never by deleting them from git.
- **A workflow with permissions it does not need** — a default-write `GITHUB_TOKEN` on a read-only
  job, `contents: write` where `contents: read` suffices, or `pull_request_target` running code from
  the fork it just checked out.
- **A shell script without `set -euo pipefail`** — plus unquoted `$var` in a path, a `cd` whose
  failure is unchecked, `curl … | sh`.
- Running as root in the final stage; `COPY . .` dragging the whole context in; an `apt-get install`
  with no `--no-install-recommends` and no cache clean; a compose service publishing a dev-only port
  on `0.0.0.0` or carrying inline credentials instead of `env_file`.

False positives — skip unless you have evidence in this repository:

- Layer-count or cache-mount micro-optimisation with no measured build-time or size win.
- The throwaway tag a local test build uses — that is the test's own tag, not a base image.
- Digest pinning where the repo's convention is tags, applied consistently.
- "Add a healthcheck" on a one-shot job container, or "use compose profiles" where one file is clear.
- A pinned old action version that the repo pins everywhere — consistency is a choice, not a defect.
