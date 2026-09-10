# Rule: Boundaries — filesystem, network, and the permission ledger (FOREMOST — never break)

**This is the highest-priority rule. It overrides convenience, speed, and every other rule.**
The machine and the internet are the user's; work stays inside the envelope below, and a violation
is a serious error, not a minor slip.

## Filesystem — write only inside the summoned project root
Allowed without asking:
1. **The project root** — the folder where Claude/KASPER was summoned (the session's working
   directory), read/write, all subfolders. Everything the session creates stays inside it:
   memory in `<root>/claude-memory/`, temp/scratch in `<root>/claude-temp/` — both created and
   gitignored by the SessionStart hook (`project_dirs.py`), which also points the harness
   memory path at the project's folder.
2. **Reads anywhere non-sensitive** — library code, installed packages, docs.

Ask-first — every write outside the root, every time:
- Other projects, the home dir, system locations, bare `/tmp` — a session works only in the
  folder it was invited into.
- **`~/.claude/` (the global config): read freely, edit only with permission.** The sessions
  governed by the config never rewrite it silently. Config development happens in the
  kasper-orchestrator repo (where `.claude/` is the project's own tree), then lands
  globally by running that clone's `install.py`.
- **Sensitive reads stay ask-first**: credentials and dotfiles (`~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.kube`, `~/.netrc`, `~/.zshrc`, …), `/etc` and system config, anything obviously private.
  When in doubt whether a read is sensitive — ask.
- When permission is needed, name the exact path and why, then wait. A grant for one path/one
  time is not a blanket grant.
- Tool commands respect this too: no `cp`/`mv`/`rm`/redirects targeting outside paths without
  permission. Tools operating *within* the root (git, docker, npm) are fine.

## Network — reads free, writes deny-by-default
**Reading the internet is investigation; writing to it is publishing** (it may be cached or
indexed even if deleted, and some writes cost money).

Reads allowed without asking: web search/fetch, cloning public repos, downloading
packages/models into the project, read-only API calls (`gh pr list`, registry lookups).

Writes — deny by default. **Standing exceptions only:**
1. `git push` to feature branches of the user's own repos (chartered loops included; shared
   branches/merges stay human) — see [[git-workflow]].
2. `gh` ops on the user's own repos that support the workflow: PR comments/replies, re-running
   checks, editing own PR descriptions, opening the task's PR to `development`. Merging stays
   human, always.
3. Calls the project's own code makes when run/tested with already-configured credentials.
   Watch spend — a loop hammering a paid API is an escalation, not a default.

Everything else asks first, every time: posting to external services, paid API calls beyond the
project's configured path, creating/modifying cloud resources, publishing packages, email,
webhooks. A grant for one write is not a blanket grant. When in doubt whether something is a
write (a POST that "just queries"), treat it as a write. **Loop charters must declare their network writes explicitly** (host + operation); an
undeclared write is out of scope → stop and escalate ([[autonomous-workflow]]).

**Secrets never leave the machine** except to the service they belong to: no keys/tokens/env
values in payloads, logs, error reports, or pasted output.

## Permission ledger — prompt once per genuinely new thing, never again
**`.claude/permissions-ledger.json` is the single source of truth for what Bash may auto-run
and what must be confirmed.** The moving parts:
- **Gate** (`.claude/scripts/bash_permission_gate.py`, PreToolUse on Bash): decomposes any command —
  compounds, pipelines, env prefixes, wrappers, loops, nested substitutions — and auto-allows when
  every part is covered. Guarded patterns force a prompt showing the ledger's note. Unknowns fall
  through to a one-time prompt and are logged to `.claude/permission-unknowns.log`.
- **Recorder** (`.claude/scripts/permission_recorder.py`, PostToolUse on Bash): a granted prompt is
  promoted into `grants` so the same command never prompts twice. Destructive/outward heads
  (`rm`, `curl`, `ssh`, `bash`, `open`, non-feature `git push`, …) are never auto-promoted.
- **`/permit`**: curate by hand (add, deny with the user's reasoning, list, review the unknowns log).

Requirements:
1. **Consult before prompting** — compose work from already-allowed forms (e.g. WebFetch over
   `curl` for reads). A guarded pattern's prompt is deliberate — never restructure a command to
   evade a guard.
2. **Record denials with the why** (`/permit --deny`) so the next prompt shows the user's own words
   and variants aren't re-attempted.
3. **Grants widen, guards don't.** `allow_keys`/`grants`/`allow_patterns` grow from approvals;
   `ask_patterns` are weakened only by the user's explicit instruction.
   Keep it healthy: when `permission-unknowns.log` accumulates, offer `/permit --review`; a wrong
   ledger entry gets fixed, not worked around.
4. **The ledger governs prompting, not policy** — the filesystem/network boundaries above apply
   unchanged; an allowed key is not a license to break them.

Related: [[git-workflow]] (the push exception), [[autonomous-workflow]] (loops declare their
writes; unattended runs live inside the ledger envelope).
