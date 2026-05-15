# Glossary

The canonical, repository-wide domain language. One global glossary for the whole
repository.

## Authority

This glossary supersedes all other documents and all code, save for reality itself.
If code, an ADR, or a comment uses a term differently, the code is wrong, not the
glossary. If reality proves a term wrong, the glossary is corrected and everything
else follows.

Changes to this file require approval from the `ai_guides/` code owner.

## How to use it

- Before naming a concept in code, tests, ADRs, specs, commits, or discussion, check
  here for the canonical term.
- If the concept is not here and it is a real domain concept, it belongs here. Add it
  (subject to owner approval).
- If you find a term used inconsistently in code you are already modifying, conform it
  (see the boy-scout rule in [domain-language.md](domain-language.md)).

## Term entry format

Each term is a level-3 heading followed by a definition. Keep definitions to behavior
and meaning, not implementation.

```
### Term Name

One or two sentences. What it is, what it is not. Reference related terms by name.
```

## Terms

<!-- Populated by the domain owner. Seed entries below are placeholders to be
     reviewed, corrected, or removed. Do not treat unreviewed entries as canonical. -->

_None confirmed yet. This section is to be populated by the domain owner._

## Deprecated term map

When a term is renamed, the old term is recorded here so that agents reading older
code, ADRs, or git history can translate. Historical artifacts are not rewritten; they
are read through this map. New work uses the current term only.

| Deprecated term | Current term | Reason | Date |
|---|---|---|---|
| _none yet_ | | | |
