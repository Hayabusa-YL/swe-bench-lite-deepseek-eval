#!/usr/bin/env python3
"""Parallel driver for swe_all.py: setup / baseline / eval across many instances.

Usage:
  python3 swe_parallel.py setup [ids-file] [--workers N] [--skip-ok]
  python3 swe_parallel.py baseline [ids-file] [--workers N]
  python3 swe_parallel.py eval [ids-file] [--workers N] [--patches DIR]

Parallelism is process-level: each job runs `python3 swe_all.py <cmd> <id>`.
Workers for setup/baseline should stay <= 6 (CPU heavy); eval <= 4.
"""
import json, os, subprocess, sys, time, glob
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.environ.get("SWE_HERE", os.path.dirname(os.path.abspath(__file__)))
WORK_NEW = "/root/swebench/work"
WORK_OLD = os.path.join(HERE, "swe-work")


def workdir(iid):
    new = os.path.join(WORK_NEW, iid)
    old = os.path.join(WORK_OLD, iid)
    return old if (os.path.isdir(old) and not os.path.isdir(new)) else new
ALL_IDS = sorted(os.path.basename(p)[:-5] for p in glob.glob("/root/swebench/instances/*.json"))


def already(cmd, iid):
    root = workdir(iid)
    if cmd == "setup":
        return os.path.exists(os.path.join(root, "setup.ok"))
    if cmd == "baseline":
        return os.path.exists(os.path.join(root, "baseline_f2p.log"))
    return os.path.exists(os.path.join(root, "final_f2p.log"))


def run_one(cmd, iid, patch=None):
    root = workdir(iid); os.makedirs(root, exist_ok=True)
    log = os.path.join(root, f"parallel_{cmd}.log")
    args = ["python3", "swe_all.py", cmd, iid]
    if cmd == "eval" and patch:
        args.append(patch)
    t0 = time.time()
    try:
        with open(log, "w") as f:
            r = subprocess.run(args, cwd=HERE, stdout=f, stderr=subprocess.STDOUT, timeout=10800)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    return iid, rc, round(time.time() - t0)


def main():
    cmd = sys.argv[1]
    ids_file = next((a for a in sys.argv[2:] if not a.startswith("--") and os.path.isfile(a)), None)
    ids = [l.strip() for l in open(ids_file) if l.strip()] if ids_file else ALL_IDS
    workers = 6; patch_dir = None
    a = sys.argv[2:]
    for i, x in enumerate(a):
        if x == "--workers": workers = int(a[i + 1])
        elif x == "--patches": patch_dir = a[i + 1]
    todo = []
    for i in ids:
        if already(cmd, i):
            continue
        if os.path.exists(os.path.join(workdir(i), "ENV_BLOCKED")):
            continue
        if cmd in ("baseline", "eval") and not os.path.exists(os.path.join(workdir(i), "setup.ok")):
            continue
        todo.append(i)
    print(f"{cmd}: {len(todo)} to run of {len(ids)} (workers={workers})", flush=True)
    ok = fail = 0; failed = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for iid in todo:
            patch = os.path.join(patch_dir, iid + ".patch") if patch_dir else None
            futs[ex.submit(run_one, cmd, iid, patch)] = iid
        for f in as_completed(futs):
            iid, rc, secs = f.result()
            if rc == 0:
                ok += 1; print(f"OK   {iid} ({secs}s)", flush=True)
            else:
                fail += 1; failed.append(iid); print(f"FAIL {iid} rc={rc} ({secs}s)", flush=True)
    print(f"{cmd} done: ok={ok} fail={fail}", flush=True)
    if failed:
        open(os.path.join(HERE, f"parallel_{cmd}_failed.txt"), "w").write("\n".join(failed) + "\n")


if __name__ == "__main__":
    main()
