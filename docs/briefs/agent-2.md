# Agent A2 Brief — Document Extraction & Validators (`extract/`)

## Ownership & Boundaries
- **Owned folders**: `extract/`, `tests/test_extract.py`
- **Forbidden folders**: All other module folders
- **Seam**: `extract/check.py:run(ctx: CaseContext) -> list[CheckResult]` and `extract/pipeline.py:extract(file_bytes: bytes) -> tuple[Invoice, InvoiceVersion, list[CheckResult]]`

## Deliverables
- `extract/gstin.py`: Mod-36 GSTIN format + checksum verification.
- `extract/layouts/`: Deterministic parsers for all 3 PDF layouts (`tabular_gst`, `letterhead`, `tally`) using `pypdf`.
- `extract/pipeline.py`: Text-layer → layout fingerprint → deterministic regex parser → optional Tesseract OCR fallback → optional Claude `claude-haiku-5-5` fallback (`LABYRINTH_ALLOW_EXTERNAL_LLM=1` only, off by default).
- `extract/validators.py`: Emits `extract.gstin_format`, `extract.gstin_checksum`, `extract.arith_lines`, `extract.arith_total`, `extract.tax_regime`, `extract.date_valid`, `extract.hsn_sac`.
- `extract/tamper.py`: Emits `extract.tamper_metadata` (`ModDate > invoice_date`, `Producer != Creator`, multiple `%%EOF` saves → at most `warn`, never `fail`).
- `extract/llm.py`: Delimited `<document>` prompt-injection resistant wrapper, off by default.
