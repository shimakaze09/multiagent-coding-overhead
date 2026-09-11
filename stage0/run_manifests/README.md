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
