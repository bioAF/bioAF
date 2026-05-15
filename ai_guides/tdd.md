# Test-Driven Development

Tests are ground truth. They prove progress and guard against regression. For agents,
they are the durable, verifiable contract: the spec is scaffolding and is discarded,
the tests are committed and outlive it.

## The loop

1. Write a failing test.
2. Write code until the test passes.
3. Commit the block (test plus the code that satisfies it). Push to remote.

Repeat. Keep the blocks small. A block is one coherent behavior, not a whole feature.

This is the default, not a hard rule. Judgment applies. But the order (failing test
first) is the point: it is what makes "it works" a true or false statement instead of
an opinion.

## Tests assert behavior, not implementation

A test verifies what the system does, not how it does it. It must survive a refactor
that preserves behavior.

Example: building user creation for an auth system. On a system with zero users,
creating a user with the name `"Jane Doe"` must result in exactly one user, with uid
`1`, name `"Jane Doe"`, and username `"janedoe"` (spaces stripped, lowercased).

The test asserts those outcomes. It does not assert which string function did the
lowercasing. If the implementation changes but the outcomes hold, the test still
passes. If the outcomes break, the test fails. That is the line.

This holds at every level. Even a unit test asserts the behavior of the unit, not its
internal calls.

## Test level

Per-feature judgment. There is no mandated level. Choose the level that proves the
behavior the feature promises: unit, integration, or end-to-end. The question is
always "what behavior am I proving true," not "what function am I covering."

## Where tests come from

For non-trivial work, tests are produced by the grilling and spec process, before
implementation. See [grilling-protocol.md](grilling-protocol.md) and
[spec-format.md](spec-format.md). The spec carries a test list; that list is the
starting contract for the build.

The test list is **not frozen**. If implementation surfaces a new edge case or failure
mode, add a test for it.

## Changing an existing test

A passing behavioral test is a promise the system already makes. Do not weaken or edit
a test just to make a change go green.

If a test must change, because it was wrong, or because intended behavior actually
changed:

1. Stop. Do not edit the test silently.
2. Flag the change to the engineer: which test, why it must change, what the new
   behavior is.
3. Get explicit permission.
4. Change the test, then resume the loop.

Adding new tests does not require this. Editing or removing existing ones does.
