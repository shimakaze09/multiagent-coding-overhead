# Raw-run manifests

`raw_run_checksums.sha256` lists the SHA-256 of every raw artifact of the three
real runs collected before the Stage 0 freeze (every file under the run
directory except the agent's `workspace/`):

| Run | Files |
| --- | --- |
| `20260910T122445Z_palindrome_punctuation_A_r1` | 12 |
| `20260910T125941Z_palindrome_punctuation_A_r1` | 12 |
| `20260910T230448Z_palindrome_punctuation_B_r1` | 38 |

The entries are identical to checksums recorded during the session. The first
Arm A run's were taken before any observability fix, and the Arm B run's before
its re-analysis.

The run directories themselves are not committed (see `.gitignore`: run
artifacts stay local). Verify a local copy with:

    sha256sum -c run_manifests/raw_run_checksums.sha256

`tests/test_run_manifest.py` performs the same check for every run present, and
also fails if a file has been added to or removed from a listed run.

## Pair 2 (second controlled A/B pair, 2026-09-11, frozen commit b506137)

`pair2_raw_run_checksums.sha256` covers the two runs of the second controlled
pair. The checksums were taken right after each run and before any re-ingest:

| Run | Files |
| --- | --- |
| `20260911T004656Z_cart_invoice_rounding_A_r1` | 15 |
| `20260911T004825Z_cart_invoice_rounding_B_r1` | 41 |

`../results/pair2_cart_invoice_rounding_report.txt` is the derived output of
`runner.py report --task cart_invoice_rounding` for that pair. It was generated
from the raw logs under the frozen analysis and reproduced byte for byte after a
SQLite rebuild. It is kept so the preregistered Pair-2 result stays readable
without the (untracked) run directories.

    sha256sum -c run_manifests/pair2_raw_run_checksums.sha256

Both manifests are historical records. The runs they list are immutable, and
no later metric replaces their preregistered results.

## Stage-0.5 mini-pilot (2026-09-11)

`stage05_pilot/raw_run_checksums.sha256` covers all 9 pilot runs (238 files).
That includes the invalid first attempt at Task 4
(`20260911T014415Z_settings_list_fields_A_r1`, `turn_limit_exceeded`,
amendment 7), which is preserved as data. Each run's checksums were taken right
after the run and before any re-ingest.

It lives in a subdirectory on purpose. `analysis.report.historical_run_ids()`
reads only top-level `run_manifests/*.sha256`, so the pilot runs keep their
Stage-0.5 metrics labelled `prospective` rather than `post_hoc_exploratory`.

Derived outputs (pair reports, cross-task summary, pilot tables) are in
`../results/stage05_pilot/`.

    sha256sum -c run_manifests/stage05_pilot/raw_run_checksums.sha256

## Stage 1: C1_shared_worker_context (2026-09-11)

`stage1_c1/raw_run_checksums.sha256` covers the four C1 runs (164 files, 41 per
run). Each run's checksums were taken right after the run and before any
re-ingest:

| Run | Files |
| --- | --- |
| `20260911T030211Z_shipping_inch_dimensions_C1_r1` | 41 |
| `20260911T030417Z_settings_list_fields_C1_r1` | 41 |
| `20260911T030731Z_rename_max_connections_C1_r1` | 41 |
| `20260911T031118Z_sla_weekend_hours_C1_r1` | 41 |

Like the pilot manifest, it lives in a subdirectory, so `historical_run_ids()`
is unchanged. Derived outputs (the four B-vs-C1 comparisons and the Stage-1
summary) are in `../results/stage1_c1/`.

    sha256sum -c run_manifests/stage1_c1/raw_run_checksums.sha256

Together, the four manifests cover all 18 real runs of the experiment.

## Stage 2, Phase C: Single-Strong calibration (2026-09-11)

`stage2/calibration_raw_run_checksums.sha256` covers every calibration attempt
so far: 37 attempts, 592 files. That includes the 3 attempts that are invalid
under the frozen rules, which are preserved as data. Checksums were taken after
the batch halted and before any re-ingest.

It lives in a subdirectory, so `historical_run_ids()` is unchanged. Attempt
records and summaries are in `../results/stage2/`, and the state of the phase
is recorded in PREREGISTRATION section 19.19.

    sha256sum -c run_manifests/stage2/calibration_raw_run_checksums.sha256
