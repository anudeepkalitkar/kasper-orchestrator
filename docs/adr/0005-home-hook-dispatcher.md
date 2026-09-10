# ADR-0005: Hooks resolve through the home dispatcher

- **Status:** accepted
- **Date:** 2026-09-10
- **Deciders:** @anudeepkalitkar (repository owner)

## Context

ADR-0001 §5 settled that every hook resolves through
`$CLAUDE_PROJECT_DIR/.claude/scripts/run_hook.py`, on the reasoning that a clone then works
in place with no install step between it and a working configuration. That reasoning holds
only for projects that carry this tree.

On 2026-09-10 the configuration was hand-copied into `~/.claude/` so that KASPER — the
skill, the agents, the rules, the commands, the hooks — is available in every project rather
than in this one. Against that install, the project-directory form is wrong in the ordinary
case: in any project that does not ship the scripts, the path names a file that is not
there, and the interpreter never reaches the dispatcher at all. `python3` exits **2** on a
file it cannot open, and Claude Code reads 2 as a blocking error — the Bash call is blocked
on `PreToolUse`, the session is refused permission to stop on `Stop`. The non-blocking exit
1 that `run_hook.py` is careful to use for a hook it cannot dispatch protects nothing here,
because `run_hook.py` is itself the missing file.

The home form raises a fair question about duplication: a project may carry its own
`settings.json` with the same hooks. The Claude Code hooks reference answers it — a handler
defined in more than one settings file runs once. That deduplication is by identity, so
byte-identical commands in the home and project settings collapse to a single run, while two
differing forms of the same intent would both fire.

## Decision

**Every hook command resolves through `python3 "$HOME/.claude/scripts/run_hook.py" <name>`,
and the home install is how KASPER is installed.** Installing means copying the contents of
`.claude/` into `~/.claude/` and merging `settings.json` into the one already there by hand;
there is no installer here yet, and the documentation says so rather than implying one.

`run_hook.py` keeps its resolution order untouched: a project's own
`.claude/scripts/<name>.py` still wins over the home copy, so a project can override an
individual hook script without owning the wiring that reaches it.

This supersedes **ADR-0001 Decision §5 only** — the part that named
`$CLAUDE_PROJECT_DIR` and claimed no install step stands between a clone and a working
config. The rest of §5 (`settings.json` carries only hook wiring and the permission block,
in portable rather than machine-specific form) and every other decision in ADR-0001 stand
unchanged.

## Alternatives considered

- **Keep `$CLAUDE_PROJECT_DIR`** — rejected: it works only inside a project that ships the
  scripts, which is the exception once the config is installed globally, and it fails
  blockingly rather than quietly everywhere else.
- **A shell prelude that tries the project path and falls back to home** — rejected on the
  same grounds `run_hook.py` was written to remove (its D11 rationale): that form is POSIX
  shell no native-Windows shell can run, and re-introducing it undoes the dispatcher's
  reason for existing.
- **Name the home directory per platform (`$USERPROFILE`, a PowerShell variant)** — rejected:
  Claude Code passes the command to Git Bash on Windows, and to PowerShell only when Git Bash
  is not installed, so `$HOME` expands in the ordinary Windows case. The PowerShell fallback
  stays a known Windows wrinkle, alongside the shipped commands naming `python3` (ADR-0001).

## Consequences

- **A project copy alone no longer runs hooks.** Cloning this repository and working inside
  it gives the skill, the agents and the rules, but the hooks stay dead until the config is
  installed at home. The claim ADR-0001 §5 made — clone and it works — is no longer true, and
  the README now says what is.
- **Home and project settings may carry identical hook lines safely.** Because deduplication
  is by identity, the single resolved form is what makes a project's copy harmless; a project
  that edits its hook commands into a different-but-equivalent form gets both, and every hook
  twice.
- **Installation becomes a real step that nothing automates.** A hand copy plus a hand merge
  of `settings.json` is error-prone and easy to leave half-done, and there is no way to tell a
  stale home copy from a current one. An installer is the natural next component.
- **Overriding a single hook script stays possible; overriding the wiring does not.** The
  project-first resolution inside `run_hook.py` is now the only override seam, which is the
  narrower and better-defined one.
