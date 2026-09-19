"""M6.3 conversion-quality measurement framework.

This package measures *conversion quality* against the M6.1 corpus and is
independent from the M6.2 structural regression harness. It is organized as:

- ``baseline``         committed expectation baseline loading + validation
- ``expectations``     expectation schema, parsing, and evaluation
- ``harness``          measurement of a converted fixture (``QualityObservation``)
- ``evaluation``       per-fixture evaluation against authored expectations
- ``report``           human-readable and machine-readable quality reports
- ``regenerate_baseline``  regenerate ``tests/fixtures/quality_baseline.json``

Key principles (same as M6.2):

* deterministic and offline -- OCR is injected
  (``tests.regression.harness.CountingOCR``), never Tesseract; no Calibre, no
  network, no clocks, no absolute paths in observations or reports;
* the classification expectation is *not* duplicated in the baseline: it is
  derived from the corpus manifest at evaluation time;
* failing expectations are findings, never silently auto-tolerated;
  ``regenerate_baseline`` refuses to ``--write`` over a failing authored
  expectation.

Marked ``quality`` so ``pytest -m quality`` selects it and ``pytest`` runs it
as part of the full suite. The ``__init__`` stays import-light (like
``tests/regression``) so the maintenance script can bootstrap ``src/`` on
``sys.path`` before importing the converter.
"""