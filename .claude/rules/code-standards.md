# Rule: Code Standards — the least code that fully works, strongly typed, documented

**The simplest thing that works: the least code that fully and clearly does the job — functions
first, hard-typed, documented.** For all production code unless a project opts out.

## The decision ladder — before writing any code
Stop at the **first rung that applies**:
1. Does it need to exist at all? Speculative or unrequested → don't build it (YAGNI); flag it.
2. Is it already in this codebase? Reuse it.
3. Does the stdlib / language built-in do it? 4. A native platform or framework feature?
5. An already-installed dependency? (A *new* dependency just to save a few lines is a deliberate
   trade-off.)
6. Can it be one clear line? Prefer it, if readable.
7. Otherwise write the **minimum that fully and clearly works**.

Minimal means instantly readable, never at the cost of correctness, error handling, security, or a
seam that earns its place. A deliberately terse choice gets `# minimal: <why>`.

## Structure — functions first, few files
1. **Deletion over addition**; fix the root cause, don't stack a symptom-patch.
2. **Functions over classes.** A class only for real state/lifecycle (a pool, a worker/app object,
   a state machine, a framework base class) — never to organize code or wrap an SDK. **A
   single-implementation ABC/Protocol is not allowed — inline it.**
3. **No unrequested abstraction or config.** Config comes from env or a small typed-settings object.
4. **Few files** — ~1–2 modules per unit, not a `config`/`client`/`service`/`models` split by reflex.
5. **Keep seams that earn their place:** real contracts, shared libraries, fake/offline test
   modes, lazy heavy-dep imports.
6. Framework idioms (route handlers, fixtures, tasks) stay as the framework models them.
7. Heavier structure is a deliberate, justified exception — say *why*.

## Hard typing
- **Python:** every signature, parameter, return and attribute annotated; no bare `Any` unless
  unavoidable (justify it in a comment).
- **TypeScript:** no implicit `any`; type params, returns, public fields.
- Precise types (`list[Document]`, `Path`, enums, `Literal`, `TypedDict`/pydantic) over loose primitives.

## Docstrings, comments, hygiene
- Every public function/method/class has a docstring: purpose, params, returns, raised errors.
- Comment the **why**; keep comments true — a stale comment is a bug.
- Small single-purpose functions; no dead code, commented-out blocks or magic numbers; handle
  errors explicitly, never swallow them; match the surrounding file.

## Imports at the top
All imports at the top, stdlib → third-party → local. **Exception — genuinely heavy deps only**
(multi-GB ML libs: `torch`/`transformers`/`paddle`): a function-level import marked
`# lazy: heavy/optional dep — keeps the base import light`. The trigger is weight, not
optional-ness; a light SDK goes at the top.

## How to apply
Walk the ladder first. When delegating, say so: functions-first, fewest files, no single-impl ABCs,
no unrequested abstraction. The autoformat hook formats Python; types and structure are yours.

Related: enforced once per task by the Codex reviewer seat; [[autonomous-workflow]], [[test-structure]].
