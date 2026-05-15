# AI Guides

The working model for development in this repository. It applies to human engineers
and to any AI agent (Claude, Cursor, Gemini, or other) working in this codebase.

Read this file first. Then read the specific guide for the work you are doing.

## Why this exists

Development here is moving from one engineer plus one agent to many engineers and
many agents. That only works if everyone shares the same language and the same
process. These guides are that shared contract.

## The pillars

1. **Shared lexicon (DDD).** We must speak about the same things in the same words.
   The glossary is the single source of truth for domain terms. See
   [glossary.md](glossary.md) and [domain-language.md](domain-language.md).

2. **Test-driven development (TDD).** Tests are ground truth. They prove progress
   without regression. Tests assert behavior, not implementation. See [tdd.md](tdd.md).

3. **Spec-driven development.** Before non-trivial work, the engineer and the agent
   build a shared, agreed understanding by questioning intent, edges, and failure
   modes ("grill me"). That understanding becomes a spec, and the spec produces the
   tests. See [grilling-protocol.md](grilling-protocol.md) and
   [spec-format.md](spec-format.md).

## Guides

| Guide | Use it when |
|---|---|
| [glossary.md](glossary.md) | You need the canonical term for a domain concept, or you are adding/changing one. |
| [domain-language.md](domain-language.md) | You need the rules for how the glossary is applied in code and ADRs. |
| [tdd.md](tdd.md) | You are writing tests or implementing against them. |
| [grilling-protocol.md](grilling-protocol.md) | You are starting non-trivial work and need shared understanding before building. |
| [spec-format.md](spec-format.md) | You are writing or reading a spec. |
| [git-conventions.md](git-conventions.md) | You are branching, committing, or pushing. |
| [writing-style.md](writing-style.md) | You are writing prose, comments, commit messages, or any other text output. |

## Scope of process

The grill -> spec -> test -> build loop is the default for non-trivial work. The
engineer decides per task whether a change is trivial enough to skip it (typo,
config tweak, doc fix). When in doubt, run the loop.

## Changing these guides

The `ai_guides/` directory is owned via CODEOWNERS. Changes require approval from
the owner. The glossary supersedes all other documents and code, save for reality
itself: if a term is wrong, the glossary is corrected and everything else follows.
