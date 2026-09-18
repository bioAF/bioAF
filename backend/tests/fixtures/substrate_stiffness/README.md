# Substrate-stiffness fixture (study 57)

A regression fixture for bioAF's own defects, never a specification. No identifier, filename, column
name, count, cutoff or sample label from it may reach production behaviour.

This is the NEGATIVE regression of plan_8_3: a paper whose five assay types are genuinely outside
bioAF's current validation methods. No repair may give it a score.

## Source

- Paper: a substrate-stiffness paper on lung adenocarcinoma cell mechanics, `10.1002/jbm.a.35655`.
  Not open access at Europe PMC; bioAF read its text from the Literature Library.
- Study 57 on the demo (`bioaf-495400`), requested by DOI on 2026-09-17, run on build `fbf20d04`
  (alembic 144). Captured read-only from the demo's database the same day.

## What is here

- `study_57_persisted.json`: the study as it was persisted, with the account columns dropped
  (organization, requesting user, approving user, uuid, and the internal paper/experiment/run ids,
  which name rows of the demo's database and nothing else). Its evidence, its reproduction plan, its
  19 comparison targets, its 0 check records and its 2 issues, verbatim otherwise.

## The two facts this fixture exists to hold apart

1. **The correct answer.** Five reported experiments (AFM elasticity, live-cell migration tracking,
   qRT-PCR, western blotting, immunofluorescence) are each `unsupported`, applicability is
   `not_applicable`, and no score is the right outcome. Section 1.3's repair is what produces this,
   and this fixture is how it stays produced.
2. **The failure that obscures it.** The finding inventory's first attempt ESTABLISHED the findings'
   membership and validated five of eight importances; three quotes did not appear in the paper's
   text, so a recovery attempt was made, and the provider answered that the account's credit balance
   was exhausted. bioAF reported that as "bioAF could not reach the language model", discarded the
   whole inventory, and the report says the scope is not established. Stage 6's account-level
   classification and its retain-what-was-established rule are verified against this.

## Bounds

- Nothing here was re-read. A recovery or a re-read spends provider credit and replaces the very
  evidence these repairs are verified against (plan_8_3 section 0.2).
- No paper text beyond the passages the read itself kept.
- No people: the requester and the approver are dropped.

## Values that must never reach production

`test_prompt_fixture_guard.py` renders every lit_validation prompt and fails if any value below appears
in one (change_7.5 section 1.6). Add to the list whenever the fixture gains one.

- `10.1002/jbm.a.35655`
- `A549`
- `PDMS`
- `TCPS`
- `Ibidi`
- `4756`
- `1520`
- `2.4 kPa`
- `p-PAX`
- `p-FAK`
- `N-cadherin`
- `E-cadherin`
