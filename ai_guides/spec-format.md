# Spec Format

A spec is the written output of the [grilling protocol](grilling-protocol.md): the
agreed understanding, captured so it can produce tests and guide a build.

## Lifecycle

- A spec is created during development by the active engineer.
- It lives in a local-only directory, `local/`, which is `.gitignore`d. It is not
  committed and not shared between engineers.
- It is single-engineer scaffolding. Whether the build happens in the same agent
  session or a fresh one is the engineer's choice; either way it stays on their
  machine.
- After the work is done, the spec is abandoned. It is not maintained.

The durable artifacts are the **tests** (committed) and any **glossary** or **ADR**
changes the work produced. The spec itself is disposable. If something in the spec
deserves to outlive it, it belongs in one of those durable artifacts, not the spec.

> Note: the repository currently uses `documentation/` for local working documents.
> The standard going forward is `local/`. Migrate when convenient.

## Mandatory contents

Every spec contains, at minimum:

- **User definition.** Who is the actor this feature serves, and what is their context.
- **Expected behavior.** What the system does when things go right.
- **Acceptance criteria.** The concrete, checkable conditions that mean "done."
- **Explicit goals.** What this work is for.
- **Explicit non-goals.** What this work deliberately does not do. The fence matters
  as much as the field.
- **Failure modes.** What should happen when things go wrong, for each identified
  failure.
- **Test list.** The tests that will prove the behavior, edges, and failure modes.
  This list seeds [TDD](tdd.md). It is the starting contract, and it is not frozen:
  implementation may add to it.

Open questions surfaced during grilling and not resolved are recorded explicitly. An
unresolved question is part of the spec, not an omission from it.

## Template

```markdown
# Spec: <feature name>

## User definition
<who, and their context>

## Goals
- <what this work is for>

## Non-goals
- <what this work deliberately does not do>

## Expected behavior
<what the system does when things go right>

## Failure modes
- <failure>: <what should happen>

## Acceptance criteria
- [ ] <concrete, checkable condition>

## Test list
- [ ] <test asserting a behavior, edge, or failure mode>

## Open questions
- <unresolved question from grilling, if any>
```
