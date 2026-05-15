# Git Conventions

## Branch naming

Format: `<engineer-id>-<purpose>`

- **engineer-id**: first initial plus last name, lowercased, maximum 7 characters.
  Brent Mills is `bmills`.
- **purpose**: a short kebab-case description of the work.
- **separator**: a single hyphen between id and purpose.

Examples: `bmills-ai-guides`, `bmills-lims-integration-api`.

Branches are cut from `main`.

## Commits

- Commit and push **frequently**. Small, coherent blocks. Under TDD, a natural commit
  is one failing test plus the code that makes it pass (see [tdd.md](tdd.md)).
- This is encouraged, not enforced. There is no hook gating it. It is the engineer's
  responsibility, and the agent should follow it by default.
- Pushing frequently means work is visible and recoverable. Local-only work that is
  never pushed is work nobody else can see and a laptop failure can erase.

## Commit messages

- Every commit message must be useful. It states what changed and, where it is not
  obvious, why.
- Sentence fragments are preferred. `Add deprecated term map to glossary`, not
  `This commit adds a deprecated term map to the glossary.`
- No em-dashes anywhere in messages.
- A message that could apply to any commit ("fix", "update", "wip") is not useful and
  is not acceptable.
