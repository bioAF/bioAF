### Naming Profiles

Replaced the original Naming Profiles feature with a parse-only, template-driven design.
See [ADR-058](decisions/ADR-058-naming-profile-parse-only.md).

- Naming Profiles now read information **from** filenames; bioAF never renames files
  based on a profile. The original closed enum of segment field names is gone, replaced
  by an unbounded, template-driven field vocabulary.
- A new wizard lets a team author a profile with three segment shapes: `number`
  (e.g. `SMP0042`), `string` (e.g. `req-bmills` with inner separator opposite the
  delimiter), and `date` (`YYYYMMDD`, `YYYY-MM-DD`, or `YYMMDD`). Non-date segments
  carry a 1-4 letter identifier so order in the filename does not matter.
- Three system segments (Project / Experiment / Sample) are always available regardless
  of which Experiment Template is selected.
- The wizard supports a live test field: paste a real filename and see what the
  in-progress profile would parse out, with unrecognized tokens and warnings.
- Clicking a saved profile opens a detail modal with an example filename, the parser
  test, and an Edit button.
- Experiment Templates and Experiments both gain an optional `naming_profile_id`.
  Experiments inherit the template's default and can override it per-experiment.
- Auto-ingest is temporarily disabled (returns 503 on `POST /api/ingest/simulate`) while
  the follow-up rework picks profile selection at parse time. Flip
  `AUTO_INGEST_DISABLED` in `app/services/auto_ingest_gate.py` to re-enable.
