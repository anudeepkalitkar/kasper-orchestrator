### For the tester

- **Gate:** `npx eslint . && npx tsc --noEmit && npm test` — use the package manager the lockfile
  names (`pnpm`/`yarn`/`bun`) and the project's own `typecheck`/`lint` scripts where they exist.
- Tests are **Vitest** (Jest only where the project already uses it), named `*.test.ts` / `*.spec.ts`
  and mirroring the source path, in `tests/unit/` and `tests/integration/`. Browser end-to-end is
  **Playwright** in `tests/e2e/`, run only when the change touches what it covers.
- React components: Testing Library — query by role, label, or text, assert what a user would see.
  No whole-tree snapshots. Fake the network with `vi.mock` or MSW; fake time with `vi.useFakeTimers`.
- A **unit** test may not touch the network, the disk, a database, the real clock, or a running dev
  server; `jsdom`/`happy-dom` is fine, it is in-process. A test that needs a real server or DB is
  `tests/integration/`.
- Never install a missing test dependency to get to green — report it; installing is a network write.

### For the reviewer

Real smells, worth a finding:

- **Implicit or silenced `any`** — an untyped parameter or public return, `as unknown as T`, a `!`
  with no guard above it, a `@ts-expect-error` with no reason, a `tsconfig` change that weakens
  strictness.
- **Effects doing data fetching** that TanStack Query or the framework's loader already owns; an
  effect that only derives state computable during render; a missing dep or a blanket
  `eslint-disable react-hooks/exhaustive-deps`; a subscription/interval/listener with no cleanup.
- **Next server/client boundary** — a `"use client"` file importing a server-only module or a DB
  client; a server component passing a whole record (token, hash) into a client one; a secret behind
  `NEXT_PUBLIC_*`/`VITE_*`; a `"use server"` action with no schema validation or no auth check.
- **Concrete accessibility misses only**: `<div onClick>` where a `<button>` belongs, an input with
  neither `<label htmlFor>` nor `aria-label`, an `<img>` with no `alt`, `target="_blank"` without
  `rel="noopener noreferrer"`, a skipped heading level, colour as the sole error signal.
- `key={index}` on a list that reorders; state mutated in place then set by the same reference.
- A floating promise in a handler, `array.forEach(async …)`, an empty `catch`, `JSON.parse` with no
  guard, `dangerouslySetInnerHTML` on unsanitised input.

False positives — skip unless you have evidence in this codebase:

- "Should be memoized" with no measured win, or on a component whose props change every render.
- "Prefer `const`" where the variable is reassigned; "possible null" where the line above narrows —
  read the whole function first.
- "Should use TypeScript" in a `.js` file the project deliberately keeps in JS.
- Hardcoded values in fixtures, stories, or docs snippets; `Math.random()` outside a crypto context.
- A promise intentionally detached and marked `void`, or one a `.catch` upstream already owns.

<!-- adapted from affaan-m/ECC (MIT) -->
