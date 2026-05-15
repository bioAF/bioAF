# Domain Language

How the [glossary](glossary.md) is applied across the codebase, ADRs, and history.

## The glossary is the source of truth

There is one global glossary for the repository. It supersedes all other documents
and all code, save for reality itself. When in conflict, the glossary wins and the
other artifact is brought into line.

## Carrying language into ADRs

- New ADRs use current glossary terms only.
- ADRs are immutable. They are not rewritten to match later term changes. An ADR is a
  record of a decision at a point in time.
- An agent reading an older ADR translates outdated terms through the deprecated term
  map in the glossary. The map is how history stays readable without being edited.
- If a decision recorded in an ADR introduces or renames a domain term, the glossary
  is updated in the same change.

## The boy-scout rule

Clean as you go. When you modify code, conform the domain terms in the code you are
already touching to the glossary.

This rule has bounds:

- It applies to code you are already modifying for another reason. It is not a mandate
  to refactor adjacent, untouched code.
- It does not justify ballooning a small diff into a repository-wide rename. A
  deliberate, repository-wide rename is its own task with its own spec.
- If conforming a term would change behavior or a public interface, stop and treat it
  as a deliberate change, not a cleanup.

## When the glossary is wrong

Reality outranks the glossary. If a term does not match how the domain actually works:

1. Raise it with the `ai_guides/` code owner.
2. On approval, correct the glossary and add a deprecated term map entry.
3. New work uses the corrected term. Existing code is migrated under the boy-scout rule
   over time, or by a dedicated rename task if urgent.
