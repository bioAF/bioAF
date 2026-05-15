# Grilling Protocol

How an engineer and an agent build shared understanding before non-trivial work.

## The goal

The goal is **not** to produce a plan and act on it. The goal is a shared, agreed
understanding of intent and behavior that both the engineer and the agent sign off
on. The plan is a byproduct. The understanding is the point.

A plan built on a misunderstanding produces working code that solves the wrong
problem. Grilling exists to kill the misunderstanding before any code is written.

## When to grill

The grill -> spec -> test -> build loop is the default for non-trivial work. The
engineer decides per task whether a change is trivial enough to skip it. Trivial means
a typo, a config tweak, a doc fix: something with no behavioral surface to
misunderstand. When in doubt, grill.

## The protocol

1. **The engineer describes the feature.** What they want, in their words.

2. **The agent questions the edges.** What are the boundaries of the desired behavior?
   Inputs, limits, ordering, empty and maximal cases, interactions with existing
   behavior. The agent asks until the edges are firmly defined, not assumed.

3. **The agent questions the failure modes.** What should happen when things go wrong?
   Bad input, missing dependencies, partial state, concurrent access, external
   failures. The agent asks until the failure behavior is firmly defined.

4. **Both sides confirm.** The agent restates the understanding. The engineer either
   corrects it or agrees. Keep questioning until there is nothing left to correct.

5. **The agent writes the spec.** Only once understanding is agreed. See
   [spec-format.md](spec-format.md).

6. **Tests are written from the spec.** The spec's behavior, edges, and failure modes
   become the test list. See [tdd.md](tdd.md).

## Conduct during grilling

- Ask one focused thing at a time, or a tight batch. Do not bury the engineer.
- Surface nuance and disagreement. If the agent thinks the intent is unclear, wrong,
  or contradictory, it says so. Sycophancy here corrupts the whole downstream process.
- Do not start building. Grilling that quietly turns into implementation has skipped
  the confirmation step.
- "I don't know yet" from the engineer is a valid answer. It marks an open question
  the spec must record, not a gap to paper over.

## Exit criterion

Grilling is done when the agent's restatement of intent, edges, and failure modes
draws no corrections from the engineer. At that point, and not before, the spec is
written.
