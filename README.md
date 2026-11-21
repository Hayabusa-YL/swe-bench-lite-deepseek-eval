# SWE-bench Lite — DeepSeek agent evaluation record

A from-scratch, Docker-free evaluation of a DeepSeek tool-calling agent on the full
[SWE-bench Lite](https://www.swebench.com/) set (300 instances), run on a single
Windows + WSL2 machine.

**Result: 116 / 300 resolved (38.7%)** under the official resolution rule
(FAIL_TO_PASS all pass; PASS_TO_PASS unchanged), evaluated in self-built
`uv` environments rather than the official Docker images.

## Contents

| Path | What |
|---|---|
| `REPORT.md` | Full record: timeline, cost, method, issues, results, alignment with the official harness |
| `results/swe-bench-lite-final.csv` | Per-instance verdict, category, patch size, baseline validity |
| `results/predictions.jsonl` | Official-format predictions (`instance_id` / `model_name_or_path` / `model_patch`) — feed to `swebench eval` for a strict official re-grade |
| `results/summary.json` | Aggregate counts |
| `scripts/swe_agent.py` | The agent loop (bash / read_file / edit_file / finish), with token & cost accounting |
| `scripts/swe_all.py` | Per-instance setup, baseline, eval |
| `scripts/swe_parallel.py` | Parallel driver |

## Verdict breakdown

| Category | Count |
|---|---|
| RESOLVED | 116 |
| No patch produced | 84 |
| Tests could not run (local env / dependency) | 40 |
| Tests ran and failed | 48 |
| PASS_TO_PASS regression | 11 |
| Patch failed to apply | 1 |

40 "could not run" and 13 baseline-unverified instances are environment artifacts
of the local setup, not agent failures — see `REPORT.md` §5 for the exact
differences from the official harness (Docker images, per-repo parsers, agent).

## Reproducing a strict official score

The patches are final; only the runner was local. On a machine with Docker:

```bash
swebench eval --dataset princeton-nlp/SWE-bench_Lite --predictions results/predictions.jsonl
```

## Notes

- All API calls used `deepseek-chat` with `temperature=0`.
- Cost figures in `REPORT.md` §1.2 are partial: token usage was not instrumented
  during the original run; `scripts/swe_agent.py` now records it per instance.
- Paths in the scripts are configurable via environment variables
  (`SWE_WORK`, `SWE_HERE`, `LLM_CRED`, `SWE_WORK_OLD`).
