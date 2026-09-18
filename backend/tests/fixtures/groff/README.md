# Groff fixture (study 34)

A regression fixture for bioAF's own defects, never a specification. No identifier, filename, column
name, count, cutoff or sample label from it may reach production behaviour. Groff is the owner's known
reproducible benchmark.

## Source

- Paper: Groff et al., *RNA-seq as a tool for evaluating human embryo competence*,
  `10.1101/gr.252981.119`, PMC6771404. An EGA deposit (controlled access): 54 samples, 108 FASTQ files.
- Study 34 on the demo (`bioaf-495400`), requested by DOI.

## What is here

- `fulltext_jats.xml`: the article's Europe PMC JATS.
- `study_34_persisted.json`: the study's state, plan, comparison targets and the evidence it recorded.
- `study_55_persisted.json`: the SAME paper read again on 2026-09-17, on build `fbf20d04` (alembic
  144), captured read-only from the demo as plan_8_3 section 0.2 requires. The account columns are
  dropped (organization, requesting user, approving user, uuid, and the internal paper/experiment/run
  ids); its evidence, plan, 20 comparison targets and 7 check records are verbatim otherwise. This is
  plan_8_3's LIVE Groff regression: its 194-gene parent count AGREES against
  `supplemental_file_3_siggenes.txt` (the same sha256 the live run recorded), and the 88-gene
  refinement of that same list is `not_checkable` for stating no significance cutoff, so section 1.2's
  subset operation is never reached on the paper it was written for.
- `supplemental_file_1_embryo_metadata.txt`, `supplemental_file_2_allrcode.docx`,
  `supplemental_file_3_siggenes.txt`: the article's public supplements.

## Values that must never reach production

`test_prompt_fixture_guard.py` renders every lit_validation prompt and fails if any value below appears
in one (change_7.5 section 1.6). Add to the list whenever the fixture gains one.

- `10.1101/gr.252981.119`
- `PMC6771404`
- `EGAS00001003667`
- `EGAD00001005044`
- `supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx`
- `194`
- `88`
- `146`
- `54`
- `51`
- `53`
- `108`
- `13175`
- `6086`
- `44.6`
- `XX vs XY WE`
- `aneuploid vs euploid WE`
- `Gardner AA vs CC`
- `high vs low morphokinetic quality WE`
