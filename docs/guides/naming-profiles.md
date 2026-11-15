# CRO Naming Profiles

A Naming Profile reads structured fields from a filename. It never renames or standardizes the file.
Profiles can use the custom-field vocabulary of an Experiment Template.

Auto-ingest is temporarily disabled during the Naming Profile redesign. Saving or testing a profile does
not enable bucket monitoring or automatic experiment/sample creation.

## Create and Test a Profile

1. Open **Data & Files > Naming Profiles** and create a profile.
2. Give it a name and description; select an Experiment Template if its fields should be offered.
3. Choose `_` or `-` as the delimiter and whether to strip file extensions.
4. Add segments with a field name and type: `string`, `number` or `date`.
5. Give each non-date segment a unique 1-4 letter identifier. Date segments have no identifier.
6. Use the test panel with representative filenames, inspect parsed values, unrecognized tokens and warnings,
   then save the profile.

The navigation entry requires infrastructure configuration access. The profile API also checks the
appropriate experiment create/edit grants. Ask your administrator for the required access if unavailable.

## Segment Rules

| Type | Filename form | Example |
| --- | --- | --- |
| Number | Identifier followed by digits | `SMP0042` |
| String | Identifier, inner separator, value | `req-bmills` with delimiter `_` |
| Date | One selected date format, without an identifier | `20261114` |

The inner separator is the opposite of the delimiter: `_` uses `-` inside a string segment, and `-` uses `_`.
Identifiers match without regard to case. Segment order is for display; tokens identify themselves, so
reordering recognized segments does not change their field meanings.

Supported dates are `YYYYMMDD`, `YYYY-MM-DD` and `YYMMDD`. A profile can have at most one date segment.
Non-date values are returned as strings, preserving leading zeros. Dates are returned in ISO form.

For example, a profile with number identifier `SMP` mapped to `sample_number`, string identifier `req`
mapped to `researcher`, and a `YYYYMMDD` date mapped to `received_date` parses:

```text
SMP0042_req-bmills_20261114.fastq.gz
```

as:

```json
{
  "sample_number": "0042",
  "researcher": "bmills",
  "received_date": "2026-11-14"
}
```

## Review Before Using Parsed Values

Check warnings and unrecognized tokens rather than assuming every filename matches completely. Parsed
values are returned by the test/parser API; the current parser path does not save them into experiment
custom fields. Template-derived field vocabulary does not imply automatic persistence or association.

The old CRO code-mapping tables, version replacement rules, profile-priority auto-selection and quarantine
workflow are not the current profile contract. See [ADR-058](../../decisions/ADR-058-naming-profile-parse-only.md)
and [Automated Pipelines](automated-pipelines.md) for the ingest limitation.
