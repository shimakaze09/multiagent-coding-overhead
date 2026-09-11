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
