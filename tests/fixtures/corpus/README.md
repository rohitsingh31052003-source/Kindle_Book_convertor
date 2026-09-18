# M6.1 Representative PDF Corpus

This directory holds the deterministic, repository-safe PDF corpus that
Milestone 6 work consumes: regression tests (M6.2), conversion-quality
measurement (M6.3), and performance measurement (M6.4).

Every fixture is **synthetic and project-authored**. No external or
copyrighted material is committed, and every PDF is generated from a
deterministic recipe, so the corpus can be regenerated and verified
byte-for-byte on any supported machine.

## Layout

```
tests/fixtures/corpus/
├── README.md              ← this file
├── manifest.json          ← machine-readable corpus manifest (generated)
├── generate_corpus.py     ← deterministic generator (single source of truth)
├── __init__.py            ← programmatic discovery API
└── pdfs/                  ← the committed fixture PDFs (generated)
```

## Regeneration and verification

Regenerate every PDF plus `manifest.json`:

```
python -m tests.fixtures.corpus.generate_corpus
```

Regenerate a single fixture (no manifest write):

```
python -m tests.fixtures.corpus.generate_corpus --only novel_basic
```

The corpus test suite (`tests/test_corpus.py`) covers:

- manifest validity, unique IDs, mandatory metadata, and paths
- presence of the generated PDFs
- **byte determinism** – regenerating a fixture into a temporary directory
  must reproduce the committed bytes exactly, apart from PyMuPDF's
  self-identification stamps (the `% Written by MuPDF <version>` header
  comment and the catalog `Producer (MuPDF <version>)` value). Those two
  stamps are masked (length-preserving, in-place) before comparison, so the
  contract holds on any supported PyMuPDF edition; all content bytes —
  object graph, streams, fonts, images, pinned metadata, xref offsets —
  are still compared exactly, and genuine drift still fails with a
  regenerate-and-recommit message
- the discovery API (`load_manifest`, `iter_documents`, `document_path`)
- `analyze_pdf` classification matches the manifest expectation
- page / text-page / image-page counts match each fixture's expectations
- key structural smoke assertions (chapters, columns, header/footer
  furniture, image placements)

## Determinism

PyMuPDF output is deterministic for a given edition with two caveats
handled by the generator:

- **Metadata**: all fixtures pin `title` and `author` via `set_metadata`;
  no creation/modification timestamps are written.
- **Trailer `/ID`**: PyMuPDF randomizes the PDF trailer identifier on every
  save. `generate_corpus.py` replaces it with a fixed constant
  (`_normalize_id`), making regeneration byte-for-byte stable.

Consequently, the committed PDFs are reproducible on PyMuPDF `>=1.24,<2`
(the range this repository supports). If you upgrade PyMuPDF, regenerate the
corpus and re-run the suite to confirm stability.

## Categories

The corpus covers the M6.1 coverage checklist:

| Category                 | Fixture(s)                    |
|--------------------------|-------------------------------|
| normal_novel             | `novel_basic`                 |
| textbook_dense           | `textbook_dense`              |
| multi_column             | `twocolumn_article`           |
| scanned_book             | `scanned_book`                |
| pdf_with_images          | `text_with_images`            |
| headers_footers          | `headers_footers`             |
| unusual_typography       | `unusual_typography`          |
| mixed_text_image         | `mixed_text_image`            |
| chapter_heavy            | `chapters_long`               |
| edge_cases               | `edge_interleaved_blank`, `edge_short_report` |

## License and provenance

All fixtures are synthetic prose written for this repository
(“Proprietary (project-authored synthetic content)” per the manifest).
The scanned-book and image pages are generated from the same synthetic
text rasterized by PyMuPDF; they contain no external images.