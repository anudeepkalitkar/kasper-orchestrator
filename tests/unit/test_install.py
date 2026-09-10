"""Unit tier for ``install.py``: the pure logic only — no disk, no clock, no network.

Every behaviour asserted here is specified by ADR-0006 §§3-10.
"""

from __future__ import annotations

import copy
from typing import Any

import install
from tests.helpers.settings import hook_commands

#: A hook command carrying :data:`install.HOOK_MARKER` — KASPER's, so the strip takes it.
_KASPER_COMMAND = 'python3 "$HOME/.claude/scripts/run_hook.py" notify'

# ---------------------------------------------------------------- constants


def test_owned_paths_are_the_adr_seven() -> None:
    """ADR-0006 §3 names exactly these seven owned paths."""
    assert install.OWNED_PATHS == (
        "agents",
        "commands",
        "rules",
        "scripts",
        "skills",
        "sounds",
        "CLAUDE.md",
    )


def test_marker_names_match_the_adr() -> None:
    """The manifest, hook marker, ledger, and settings names are fixed by ADR-0006."""
    assert install.MANIFEST_NAME == "kasper-manifest.json"
    assert install.HOOK_MARKER == "run_hook.py"
    assert install.LEDGER_NAME == "permissions-ledger.json"
    assert install.SETTINGS_NAME == "settings.json"


# ---------------------------------------------------------------- default_python


def test_default_python_is_python_on_windows() -> None:
    """Windows has no ``python3`` on PATH (ADR-0006 §2)."""
    assert install.default_python("win32") == "python"


def test_default_python_is_python3_elsewhere() -> None:
    """Every non-Windows platform keeps ``python3``."""
    assert install.default_python("darwin") == "python3"
    assert install.default_python("linux") == "python3"


# ---------------------------------------------------------------- sha256_hex


def test_sha256_hex_matches_the_known_empty_digest() -> None:
    """A published vector pins the algorithm and the hex encoding."""
    assert install.sha256_hex(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_sha256_hex_is_stable_and_distinguishes_content() -> None:
    """Same bytes hash the same; one changed byte does not."""
    assert install.sha256_hex(b"kasper") == install.sha256_hex(b"kasper")
    assert install.sha256_hex(b"kasper") != install.sha256_hex(b"kaspeR")


# ---------------------------------------------------------------- render_hooks


def test_render_hooks_rewrites_the_leading_interpreter(
    kasper_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """``python3 `` at the head of a hook command becomes the chosen interpreter (§2)."""
    rendered = install.render_hooks(kasper_hooks, "python")
    assert rendered["PreToolUse"][0]["hooks"][0]["command"] == (
        'python "$HOME/.claude/scripts/run_hook.py" bash_permission_gate'
    )
    assert rendered["Stop"][0]["hooks"][0]["command"] == (
        'python "$HOME/.claude/scripts/run_hook.py" notify'
    )


def test_render_hooks_keeps_the_literal_home_variable(
    kasper_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """``$HOME`` stays literal — Git Bash expands it on Windows (§11)."""
    command = install.render_hooks(kasper_hooks, "python")["Stop"][0]["hooks"][0]["command"]
    assert '"$HOME/.claude/scripts/run_hook.py"' in command


def test_render_hooks_leaves_other_commands_untouched() -> None:
    """Only a leading ``python3 `` is rewritten; anything else is the user's business."""
    hooks: dict[str, list[dict[str, Any]]] = {
        "PostToolUse": [
            {
                "hooks": [
                    {"type": "command", "command": "prettier --write"},
                    {"type": "command", "command": "/usr/bin/env python3 tool.py"},
                    {"type": "command", "command": "echo python3 is not the head"},
                ]
            }
        ]
    }
    assert hook_commands(install.render_hooks(hooks, "python")) == [
        "prettier --write",
        "/usr/bin/env python3 tool.py",
        "echo python3 is not the head",
    ]


def test_render_hooks_leaves_a_versioned_interpreter_alone() -> None:
    """Only the exact ``python3 `` head is rewritten — ``python3.12`` is a different command."""
    hooks: dict[str, list[dict[str, Any]]] = {
        "Stop": [
            {
                "hooks": [
                    {"type": "command", "command": "python3.12 -m mytool"},
                    {"type": "command", "command": "python3"},
                ]
            }
        ]
    }
    assert hook_commands(install.render_hooks(hooks, "python")) == [
        "python3.12 -m mytool",
        "python3",
    ]


def test_render_hooks_preserves_handler_fields(
    kasper_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """Matchers, types, and timeouts survive rendering unchanged."""
    group = install.render_hooks(kasper_hooks, "python")["PreToolUse"][0]
    assert group["matcher"] == "Bash"
    assert group["hooks"][0]["type"] == "command"
    assert group["hooks"][0]["timeout"] == 600


def test_render_hooks_does_not_mutate_its_input(
    kasper_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """The caller's hook block is deep-copied, never edited in place."""
    before = copy.deepcopy(kasper_hooks)
    install.render_hooks(kasper_hooks, "python")
    assert kasper_hooks == before


def test_render_hooks_with_python3_is_an_identity(
    kasper_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """The default interpreter leaves the commands exactly as authored."""
    assert install.render_hooks(kasper_hooks, "python3") == kasper_hooks


# ---------------------------------------------------------------- strip_kasper_hooks


def test_strip_kasper_hooks_removes_handlers_from_every_event(
    home_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """§4: the strip covers every event in the file, not only the ones KASPER defines."""
    remaining = hook_commands(install.strip_kasper_hooks(home_hooks))
    assert not any(install.HOOK_MARKER in command for command in remaining)


def test_strip_kasper_hooks_prunes_emptied_groups_and_events(
    home_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """An emptied matcher group goes, and an event left with no groups goes with it."""
    stripped = install.strip_kasper_hooks(home_hooks)
    assert "SessionEnd" not in stripped
    assert [group["matcher"] for group in stripped["PreToolUse"]] == ["Write"]


def test_strip_kasper_hooks_keeps_user_handlers_in_order(
    home_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """Handlers naming any other command are untouched, order included."""
    stripped = install.strip_kasper_hooks(home_hooks)
    assert stripped["PreToolUse"][0]["hooks"][0]["command"] == "prettier --write"
    assert stripped["UserPromptSubmit"] == [
        {"hooks": [{"type": "command", "command": "my-logger.sh"}]}
    ]


def test_strip_kasper_hooks_is_a_no_op_without_kasper_wiring() -> None:
    """A hook block KASPER never touched comes back equal."""
    hooks: dict[str, list[dict[str, Any]]] = {
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]
    }
    assert install.strip_kasper_hooks(hooks) == hooks


def test_strip_kasper_hooks_does_not_mutate_its_input(
    home_hooks: dict[str, list[dict[str, Any]]],
) -> None:
    """The home hook block is never edited in place."""
    before = copy.deepcopy(home_hooks)
    install.strip_kasper_hooks(home_hooks)
    assert home_hooks == before


# ---------------------------------------------------------------- merge_settings


def test_merge_settings_preserves_unrelated_top_level_keys(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """§4: model, theme, and everything else the user set stay exactly as found."""
    merged = install.merge_settings(home_settings, kasper_settings, "python3")
    assert merged["model"] == "opus"
    assert merged["theme"] == "dark"
    assert merged["statusLine"] == {"type": "command", "command": "my-status.sh"}


def test_merge_settings_preserves_other_permission_keys(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """Only ``allow`` and ``deny`` are merged; sibling permission keys are left alone."""
    merged = install.merge_settings(home_settings, kasper_settings, "python3")
    assert merged["permissions"]["additionalDirectories"] == ["/work"]


def test_merge_settings_unions_allow_and_deny_keeping_existing_order(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """KASPER's entries are appended where absent; the user's order is kept; no duplicates."""
    merged = install.merge_settings(home_settings, kasper_settings, "python3")
    assert merged["permissions"]["allow"] == ["Bash(ls*)", "Read", "WebFetch"]
    assert merged["permissions"]["deny"] == ["Bash(sudo *)", "Bash(rm -rf *)"]


def test_merge_settings_replaces_previous_kasper_hooks_rather_than_doubling_them(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """Stale KASPER wiring is stripped first, so the count matches KASPER's own."""
    merged = install.merge_settings(home_settings, kasper_settings, "python3")
    installed = [c for c in hook_commands(merged["hooks"]) if install.HOOK_MARKER in c]
    assert len(installed) == 2
    assert "retired_cleanup" not in " ".join(installed)


def test_merge_settings_appends_kasper_groups_after_user_groups(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """In a shared event the user's group stays first and KASPER's is appended."""
    merged = install.merge_settings(home_settings, kasper_settings, "python3")
    matchers = [group.get("matcher") for group in merged["hooks"]["PreToolUse"]]
    assert matchers == ["Write", "Bash"]


def test_merge_settings_adds_events_the_home_file_lacked(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """An event only KASPER defines is created."""
    merged = install.merge_settings(home_settings, kasper_settings, "python3")
    assert merged["hooks"]["Stop"][0]["hooks"][0]["command"].endswith("notify")


def test_merge_settings_renders_the_chosen_interpreter(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """The merged hooks carry the interpreter passed in, not a hardcoded one."""
    merged = install.merge_settings(home_settings, kasper_settings, "python")
    assert merged["hooks"]["Stop"][0]["hooks"][0]["command"].startswith("python ")


def test_merge_settings_into_an_empty_file_installs_kasper_wholesale(
    kasper_settings: dict[str, Any],
) -> None:
    """No existing settings means KASPER's hooks and permissions land as they are."""
    merged = install.merge_settings({}, kasper_settings, "python3")
    assert merged["hooks"] == kasper_settings["hooks"]
    assert merged["permissions"]["allow"] == ["Read", "WebFetch"]


def test_merge_settings_writes_no_empty_permissions_block() -> None:
    """Neither side has permissions, so the merged file gains no hollow key."""
    merged = install.merge_settings({}, {"hooks": {}}, "python3")
    assert "permissions" not in merged


def test_merge_settings_adds_no_hollow_hooks_key() -> None:
    """No hooks on either side means the user's file gains no empty ``hooks`` object."""
    merged = install.merge_settings({"model": "opus"}, {}, "python3")
    assert merged == {"model": "opus"}


def test_merge_settings_keeps_a_hooks_key_the_strip_emptied() -> None:
    """A file that *had* hooks keeps the key even when only KASPER's were in it.

    Removing a key the user's file already carried is not this script's business; the
    value is simply the empty block the strip left.
    """
    existing: dict[str, Any] = {
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": _KASPER_COMMAND}]}]}
    }
    merged = install.merge_settings(existing, {}, "python3")
    assert merged["hooks"] == {}


def test_merge_settings_does_not_mutate_the_existing_file(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """The loaded home settings are read-only input — the backup must stay truthful."""
    before = copy.deepcopy(home_settings)
    install.merge_settings(home_settings, kasper_settings, "python3")
    assert home_settings == before


def test_merge_settings_is_idempotent(
    home_settings: dict[str, Any], kasper_settings: dict[str, Any]
) -> None:
    """Re-installing over an installed home changes nothing — the basis of §6's no-op run."""
    once = install.merge_settings(home_settings, kasper_settings, "python3")
    twice = install.merge_settings(once, kasper_settings, "python3")
    assert twice == once


# ---------------------------------------------------------------- merge_ledger


def test_merge_ledger_copies_the_seed_when_no_home_ledger_exists(
    seed_ledger: dict[str, Any],
) -> None:
    """§5: absent at home, the repository seed is taken as is."""
    assert install.merge_ledger(None, seed_ledger) == seed_ledger


def test_merge_ledger_adds_missing_allow_keys_without_touching_existing_notes(
    home_ledger: dict[str, Any], seed_ledger: dict[str, Any]
) -> None:
    """Absent keys are added; a key the user already annotated keeps their note."""
    merged = install.merge_ledger(home_ledger, seed_ledger)
    assert merged["allow_keys"]["ls"] == "read-only"
    assert merged["allow_keys"]["pytest"] == "tests"
    assert merged["allow_keys"]["cd"] == "the user's own note"


def test_merge_ledger_never_removes_anything_from_the_home_ledger(
    home_ledger: dict[str, Any], seed_ledger: dict[str, Any]
) -> None:
    """A machine-local key absent from the seed survives the merge."""
    assert install.merge_ledger(home_ledger, seed_ledger)["allow_keys"]["brew"] == "mine"


def test_merge_ledger_adds_patterns_absent_by_pattern_only(
    home_ledger: dict[str, Any], seed_ledger: dict[str, Any]
) -> None:
    """Entries match on ``pattern``; a pattern already present keeps the user's note."""
    merged = install.merge_ledger(home_ledger, seed_ledger)
    assert merged["allow_patterns"] == seed_ledger["allow_patterns"]
    notes = {entry["pattern"]: entry["note"] for entry in merged["ask_patterns"]}
    assert notes[r"\brm\s+-[a-zA-Z]*[rf]"] == "my own wording"
    assert notes[r"\bgh\s+pr\s+merge\b"] == "merges are human-only"


def test_merge_ledger_leaves_grants_denials_and_doc_exactly_as_found(
    home_ledger: dict[str, Any], seed_ledger: dict[str, Any]
) -> None:
    """§5: grants are per-machine; only the seed is portable."""
    merged = install.merge_ledger(home_ledger, seed_ledger)
    assert merged["grants"] == [{"command": "npm test", "note": "approved 2026-09-01"}]
    assert merged["denials"] == [{"pattern": r"\bcurl\b", "note": "no outbound writes"}]
    assert merged["_doc"] == "the user's own documentation"


def test_merge_ledger_seeds_a_home_ledger_that_is_an_empty_object(
    seed_ledger: dict[str, Any],
) -> None:
    """§5: an empty (not absent) home ledger gains every seed key and pattern."""
    merged = install.merge_ledger({}, seed_ledger)
    assert merged["allow_keys"] == seed_ledger["allow_keys"]
    assert merged["allow_patterns"] == seed_ledger["allow_patterns"]
    assert merged["ask_patterns"] == seed_ledger["ask_patterns"]


def test_merge_ledger_does_not_mutate_the_existing_ledger(
    home_ledger: dict[str, Any], seed_ledger: dict[str, Any]
) -> None:
    """The home ledger is read-only input so the backup stays truthful."""
    before = copy.deepcopy(home_ledger)
    install.merge_ledger(home_ledger, seed_ledger)
    assert home_ledger == before


def test_merge_ledger_is_idempotent(
    home_ledger: dict[str, Any], seed_ledger: dict[str, Any]
) -> None:
    """A second install adds nothing further."""
    once = install.merge_ledger(home_ledger, seed_ledger)
    assert install.merge_ledger(once, seed_ledger) == once


# ---------------------------------------------------------------- plan_files


def test_plan_files_classifies_create_overwrite_and_unchanged() -> None:
    """§8's plan words, decided by bytes alone."""
    source = {"a.md": b"new", "b.md": b"same", "c/d.md": b"changed"}
    existing = {"b.md": b"same", "c/d.md": b"old"}
    assert install.plan_files(source, existing) == {
        "a.md": "create",
        "b.md": "unchanged",
        "c/d.md": "overwrite",
    }


def test_plan_files_ignores_files_the_source_does_not_own() -> None:
    """A home file KASPER never ships is none of the installer's business."""
    plan = install.plan_files({"a.md": b"x"}, {"a.md": b"x", "personal.md": b"mine"})
    assert plan == {"a.md": "unchanged"}


def test_plan_files_of_nothing_is_empty() -> None:
    """An empty source plans no work."""
    assert install.plan_files({}, {"a.md": b"x"}) == {}


# ---------------------------------------------------------------- status_report


def test_status_report_marks_ok_modified_and_missing() -> None:
    """§9: each manifest entry is judged against the current hash."""
    manifest = {"a.md": "aaa", "b.md": "bbb", "c.md": "ccc"}
    current: dict[str, str | None] = {"a.md": "aaa", "b.md": "zzz", "c.md": None}
    assert install.status_report(manifest, current) == {
        "a.md": "ok",
        "b.md": "modified",
        "c.md": "missing",
    }


def test_status_report_judges_only_what_the_manifest_recorded() -> None:
    """§9 speaks for the recorded files; a home file KASPER never installed is not drift."""
    current: dict[str, str | None] = {"a.md": "aaa", "personal.md": "mine"}
    assert install.status_report({"a.md": "aaa"}, current) == {"a.md": "ok"}


def test_status_report_of_an_empty_manifest_is_empty() -> None:
    """Nothing recorded, nothing to report."""
    assert install.status_report({}, {}) == {}


# ---------------------------------------------------------------- uninstall_plan


def test_uninstall_plan_deletes_only_untouched_files() -> None:
    """§10: a modified file is kept, an already-gone file is reported missing."""
    manifest = {"a.md": "aaa", "b.md": "bbb", "c.md": "ccc"}
    current: dict[str, str | None] = {"a.md": "aaa", "b.md": "zzz", "c.md": None}
    assert install.uninstall_plan(manifest, current) == {
        "a.md": "delete",
        "b.md": "keep",
        "c.md": "missing",
    }


def test_uninstall_plan_of_an_empty_manifest_is_empty() -> None:
    """Nothing recorded, nothing to remove."""
    assert install.uninstall_plan({}, {}) == {}
