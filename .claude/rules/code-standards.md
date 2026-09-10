# Rule: Code Standards — the least code that fully works, strongly typed, documented

**Prefer the simplest thing that works: the least code that fully and clearly does the job —
functions first, hard-typed, documented.** One coherent stance for all production code in every
project unless a project explicitly opts out.

## The decision ladder — run this BEFORE writing any code
Stop at the **first rung that applies**, then stop climbing:
1. **Does this need to exist at all?** The best code is the code you never write. Speculative,
   unrequested, "we might need it" → **don't build it** (YAGNI). Flag it instead of guessing.
2. **Is it already in this codebase?** Reuse the existing function/type/helper.
3. **Does the stdlib / language built-in do it?** Use it.
4. **Does a native platform / framework feature do it?** Use it instead of hand-rolling.
5. **Does an already-installed dependency do it?** Use it. (Don't add a *new* dependency just to
   save a few lines — weigh that trade-off deliberately.)
6. **Can it be one clear line/expression?** Prefer it — as long as it stays readable.
7. **Otherwise**, write the **minimum that fully and clearly works** — and no more.

**Balance (not code golf).** Minimal means *the least code a competent reader instantly
understands* — never cryptic one-liners, never at the cost of correctness, readability, error
handling, security, accessibility, or a seam that earns its place. A deliberately terse choice
that might look unfinished gets a `# minimal: <why>` comment so it isn't "helpfully" expanded.

## Structure — functions first, few files
1. **Deletion over addition; shortest working diff.** Fix the **root cause**, don't stack a
   symptom-patch. The best change is often a smaller one.
2. **Functions over classes.** Reach for a class only for genuine state/lifecycle a function
   can't express cleanly: a long-lived connection/pool, a worker/app object, a stateful
   protocol/state machine, a framework base class you must subclass. Never a class to organize
   code, wrap an API/SDK client, or hold "domain logic" that is really a few functions.
   **A single-implementation ABC/Protocol/interface is not allowed — inline it**; abstract only
   when a second real implementation exists or is clearly imminent.
3. **No unrequested abstraction or config.** No "flexible" knobs nobody asked for, no generic
   framework for one concrete case. Config comes from env or a small typed-settings object —
   not a bespoke settings class per component.
4. **Few files.** A thin unit is ~1–2 modules, not a `config`/`client`/`service`/`handlers`/
   `models` split by reflex. Split only when it genuinely helps reading.
5. **Keep the seams that earn their place.** Real contracts between components, shared libraries,
   fake/offline test modes, and lazy heavy-dep imports are not ceremony — they remove duplication
   and enable testing. Delete ceremony, never the architecture's genuine boundaries.
6. **Framework idioms as-is.** Route handlers, dependencies, fixtures, tasks that a framework
   models as functions stay functions.
7. A project that genuinely needs heavier structure (large stateful domain, many implementations)
   is a deliberate, justified exception — say *why*, don't default into it.

## Hard typing (no untyped code)
- **Python**: every signature, parameter, return, and attribute fully annotated. No bare `Any`
  unless genuinely unavoidable — justify it in a comment.
- **TypeScript/JS**: prefer TS; no implicit `any`; type all params, returns, public fields.
- **Precise types** (`list[Document]`, `Path`, enums, `Literal`, `TypedDict`/pydantic,
  discriminated unions) over loose primitives — make illegal states unrepresentable.

## Docstrings and comments
- Every public function/method/class has a **docstring**: purpose, params, returns, raised errors.
- Comment the **why**, not the *what*, where logic is non-obvious.
- Comments stay **truthful and in sync** — a stale comment is a bug.

## Hygiene
- Small, single-responsibility functions; names that say what they do.
- No dead code, no commented-out blocks, no magic numbers (name them).
- Handle errors explicitly — never swallow exceptions silently.
- Match the surrounding file's naming, structure, and idioms.

## Imports at the top
- All imports at the top of the module, grouped stdlib → third-party → local. Never scattered
  inside functions — except the one exception below.
- **Lazy-import exception — genuinely *heavy* deps only**: a function-level import is allowed only
  for a dependency heavy enough that a top-level import would force a large/unwanted install just
  to import the module or run the offline gate (multi-GB ML libs: `torch`/`transformers`/
  `paddle`). The trigger is **weight**, not optional-ness — a light SDK (a few MB), even an
  optional `[extra]`, goes at the top as a hard dependency. Mark deliberate uses:
  `# lazy: heavy/optional dep — keeps the base import light`. (No per-call cost —
  `sys.modules` caches; if cold-start matters, warm the import at startup.)

## How to apply
Walk the ladder first; write to the strictest reasonable typing and the simplest structure that
works. When delegating to subagents, say explicitly: functions-first, fewest files, walk the
ladder — no single-impl ABCs, no unrequested abstraction, no file-per-concern layout. The
autoformat hook formats Python but does **not** add types or structure — that responsibility is
yours.

Related: enforced per-PR by the `code-reviewer` agent; complements [[autonomous-workflow]]
(plan → verify) and [[test-structure]] (small focused tests, not elaborate frameworks).
