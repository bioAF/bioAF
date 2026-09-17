# Panahipour fixture (study 50)

A regression fixture for bioAF's own defects, never a specification. No identifier, filename, column
name, count, cutoff or sample label from it may reach production behaviour.

## Source

- Paper: Panahipour et al., *RNA-Seq of Gingival Fibroblasts Grown on Collagen Membranes and
  Hyaluronic Acid*, `10.3390/jfb17020057`, PMC12942325.
- Deposit: `GSE309060`. Matrix `GSE309060_raw_counts_All_Samples.tsv.gz`, 1,157,801 bytes,
  md5 `3cd648037a4b7edcf7842c9056e8ca3d`, sha256
  `fb7908279ff4729cee9b65418240c6d8c61fe789a5112b0a717a8ad8a30ee5ad`, gzip, UTF-8,
  5,293,337 decoded bytes. 58,780 data rows.
  `https://ftp.ncbi.nlm.nih.gov/geo/series/GSE309nnn/GSE309060/suppl/GSE309060_raw_counts_All_Samples.tsv.gz`
- Study 50 on the demo (`bioaf-495400`), requested by DOI, read at build `717ba5ba`.

## What is here

- `raw_counts_excerpt.tsv`: twelve rows in the deposit's own shape, under the deposit's own header.
  sha256 `976289a12de06bcf29d1bb3170b2ac71c6dc581bfc3936fd14020705e70b2568`.
- `sample_records.json`: the repository's 21 sample records as the read recorded them, verbatim.

## The column roles this fixture exists to establish

The header is `GeneType`, `GeneSymbol`, `ENSG`, then the 21 measurement columns. bioAF read the FIRST
column as the feature identifier and every other column as a sample, so it reported 23 samples, gave
`GeneSymbol` and `ENSG` zero library sizes, and would have sent a biotype into the analysis template
as the gene identifier.

- feature identifier: `ENSG` (a distinct Ensembl gene identifier per row)
- annotations: `GeneType` (a biotype repeated across thousands of rows), `GeneSymbol` (a symbol the
  deposit repeats and sometimes leaves blank)
- measurements: the 21 columns each of the repository's sample records names by its title

The excerpt carries the two shapes that must not pass unnoticed: one symbol on two Ensembl
identifiers, and a row whose symbol is blank.

## The identity evidence this fixture carries, and its limit

Each sample record states tissue, cell type and treatment. NONE states a donor. The paper reports
three independent donors; that does not establish which record belongs to which donor, and it does
not establish that the treatment groups are unpaired. Donor identity is unresolved here, and any
test that needs a donor mapping must supply it as a recorded confirmation, not infer it from the LP
numbering.

## Values that must never reach production

`test_prompt_fixture_guard.py` renders every lit_validation prompt and fails if any value below
appears in one. Add to the list whenever the fixture gains one.

- `10.3390/jfb17020057`
- `PMC12942325`
- `GSE309060`
- `GSE309060_raw_counts_All_Samples.tsv.gz`
- `GeneType`
- `GeneSymbol`
- `Mucoderm`
- `Collafleece`
- `gingival fibroblast`
- `58780`
- `99`
- `114`
