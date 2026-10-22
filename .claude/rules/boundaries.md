# Rule: Boundaries — filesystem, network, and the permission ledger (FOREMOST — never break)

**The highest-priority rule: it overrides convenience, speed, and every other rule.** A violation
is a serious error, not a slip.

## Filesystem — write only inside the summoned project root
Allowed without asking:
1. **The project root** — the session's working directory, read/write. Memory goes in
   `<root>/claude-memory/`, scratch in `<root>/claude-temp/`; both are created by the SessionStart
   hook (`project_dirs.py`), which keeps them and `/tasks/` out of git through the repo's **local**
   `.git/info/exclude` — never the shared `.gitignore`. `/docs/adr/` joins them only where the
   decision record stays local: the hook asks `gh` for the repo's visibility once per session and,
   in a `PRIVATE` repo, leaves that entry out (dropping one an earlier run wrote). It also points
   the harness memory path at the project's folder. Nothing cleans scratch up; the human clears
   `claude-temp/` by hand.
2. **Reads anywhere non-sensitive.**

Ask first — every write outside the root, every time: other projects, the home dir, system
locations, bare `/tmp`. **`~/.claude/` is read freely, edited only with permission**; config is
developed in the kasper-orchestrator repo and installed by `install.py`. **Sensitive reads stay
ask-first**: credentials and dotfiles (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`, `~/.netrc`,
`~/.zshrc`, …), `/etc`, anything obviously private. Name the exact path and why, then wait; a
grant for one path/one time is not a blanket grant. No `cp`/`mv`/`rm`/redirects to outside paths.

**One scoped exception:** the Codex **tester seat** runs under `--sandbox workspace-write` (workdir
+ `/tmp` + `$TMPDIR`; `.git/`, `$HOME` and the network blocked — verified 2026-09-16). That `/tmp`
write is accepted for that seat only.

## Network — reads free, writes deny-by-default
Reads are free: web search/fetch, cloning public repos, downloading packages into the project,
read-only API calls. **Writes** are publishing (cached, indexed, sometimes paid); standing
exceptions only:
1. `git push` to feature branches of the user's own repos ([[git-workflow]]).
2. `gh` ops on the user's own repos that support the workflow: PR comments, own-PR descriptions,
   opening the task's PR. Merging stays human.
3. Calls the project's own code makes with already-configured credentials. A loop hammering a paid
   API is an escalation, not a default.

Everything else asks first — external posts, paid APIs beyond the configured path, cloud changes,
publishing, email, webhooks. When unsure whether a call is a write (a POST that "just queries"),
treat it as one. **Loop charters declare their network writes (host + operation)**; an undeclared
one escalates. **Secrets never leave the machine** except to the service they belong to.

## Permission ledger — prompt once per genuinely new thing
`.claude/permissions-ledger.json` is the single source of truth for what Bash may auto-run.
- **Gate** (`bash_permission_gate.py`, PreToolUse): decomposes any command and auto-allows when
  every part is covered. Guards force a prompt and beat every allow and grant: `ask_words` (a tool
  plus guarded words — `git` + `merge`) and `ask_patterns` (regex). Unknowns fall through to a
  one-time prompt and are logged to `.claude/permission-unknowns.log`.
- **Recorder** (`permission_recorder.py`, PostToolUse): a granted prompt becomes a `grant`;
  destructive/outward heads (`rm`, `curl`, `ssh`, `bash`, `open`, non-feature `git push`, …) never
  auto-promote. **`/permit`** curates by hand.

Compose from allowed forms (WebFetch over `curl`); never restructure a command to evade a guard.
Record denials with the why (`/permit --deny`). Grants widen, guards don't: `ask_words` and
`ask_patterns` weaken only on the user's explicit instruction, and a wrong entry is fixed, not
worked around. The ledger governs prompting, not policy.

Related: [[git-workflow]], [[autonomous-workflow]].
