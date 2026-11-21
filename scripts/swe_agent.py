#!/usr/bin/env python3
"""Minimal tool-calling agent for SWE-bench Lite (no Docker).

For each instance id:
  - reset repo-clean to base commit (git checkout/clean, keep build artifacts)
  - run a ReAct loop against an OpenAI-compatible chat API
  - collect `git diff` into swe-work/<id>/agent/fix.patch

Usage:
  python3 swe_agent.py run <ids-file> [--workers 3] [--steps 30] [--model deepseek-chat]
  python3 swe_agent.py one <instance-id>
"""
import json, os, subprocess, sys, time, re, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

WORK_NEW = "/root/swebench/work"
WORK_OLD = os.environ.get("SWE_WORK_OLD", "")

# 估算用单价（美元 / 百万 token）；默认按 deepseek-chat 公开价，可被环境变量覆盖
PRICE = {
    "in_miss": float(os.environ.get("DS_PRICE_IN_MISS", "0.27")),
    "in_hit": float(os.environ.get("DS_PRICE_IN_HIT", "0.07")),
    "out": float(os.environ.get("DS_PRICE_OUT", "1.10")),
}


def _usage_tokens(resp):
    u = resp.get("usage") or {}
    return {
        "prompt_tokens": int(u.get("prompt_tokens") or 0),
        "completion_tokens": int(u.get("completion_tokens") or 0),
        "prompt_cache_hit_tokens": int(u.get("prompt_cache_hit_tokens") or 0),
        "prompt_cache_miss_tokens": int(u.get("prompt_cache_miss_tokens")
                                        or (u.get("prompt_tokens") or 0)),
    }


def estimate_cost(t):
    miss = t["prompt_cache_miss_tokens"] or t["prompt_tokens"]
    hit = t["prompt_cache_hit_tokens"]
    return round((miss * PRICE["in_miss"] + hit * PRICE["in_hit"]
                  + t["completion_tokens"] * PRICE["out"]) / 1e6, 6)


def workdir(iid):
    new = os.path.join(WORK_NEW, iid)
    old = os.path.join(WORK_OLD, iid)
    return old if (os.path.isdir(old) and not os.path.isdir(new)) else new
CRED = os.environ.get("LLM_CRED", os.path.expanduser("~/.dsh/llm-providers.json"))

SYSTEM = """You are a software engineer fixing a real bug in a Python repository.
You are inside the repository checkout (your shell cwd). The issue describes the bug; a hidden test suite will check the fix.

Work like a careful engineer:
1. Read the issue, then locate relevant code with grep/read.
2. Understand the bug and reproduce it mentally or with a small python snippet.
3. Make a minimal, correct code change (edit source files only; never modify tests).
4. Optionally run a small targeted command to sanity-check your change.
5. When done, call finish with a one-line summary.

Rules:
- Do NOT edit test files. Do NOT run the full test suite.
- Never run pytest/pip/network commands; keep shell commands lightweight (grep/ls/python snippet), under ~10 seconds.
- Keep changes minimal and focused on the reported issue.
- Use the tools: bash (shell), read_file, edit_file, finish.
- If you run out of ideas, still leave your best patch in the tree and call finish.
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "Run a shell command inside the repo and return stdout+stderr (truncated).",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file with line numbers.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start": {"type": "integer", "description": "1-based first line (optional)"},
            "limit": {"type": "integer", "description": "max lines (default 200)"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "edit_file", "description": "Replace an exact substring in a file. old_string must match exactly once (unless replace_all).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"}},
            "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {
        "name": "finish", "description": "End the task. The diff in the working tree is your submission.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string"}}, "required": ["summary"]}}},
]


def load_credentials():
    d = json.load(open(CRED))
    for p in d["providers"]:
        if p.get("name") == "deepseek" and p.get("apiKey"):
            return p["baseUrl"].rstrip("/"), p["apiKey"], p.get("model") or "deepseek-chat"
    raise SystemExit("no deepseek provider in llm-providers.json")


def api_call(base, key, model, messages, tools, max_tokens=4096):
    body = json.dumps({"model": model, "messages": messages, "tools": tools,
                       "tool_choice": "auto", "temperature": 0.0,
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(5 * (attempt + 1)); continue
            raise SystemExit(f"API error {e.code}: {msg}")
        except Exception:
            if attempt < 3:
                time.sleep(5 * (attempt + 1)); continue
            raise
    raise SystemExit("API retries exhausted")


def apply_tool(repo, name, args):
    if name == "bash":
        cmd = args.get("command", "")
        try:
            r = subprocess.run(cmd, shell=True, cwd=repo, capture_output=True,
                               text=True, timeout=60)
            out = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return "command timed out after 60s; do not run long/heavy commands, prefer reading files"
        if len(out) > 6000:
            out = out[:6000] + "\n... [truncated]"
        return out or "(no output)"
    if name == "read_file":
        path = os.path.join(repo, args["path"])
        start = max(1, int(args.get("start") or 1)); limit = int(args.get("limit") or 200)
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError as e:
            return f"error: {e}"
        seg = lines[start - 1:start - 1 + limit]
        return "\n".join(f"{start + i:5d}\t{l}" for i, l in enumerate(seg)) or "(empty)"
    if name == "edit_file":
        path = os.path.join(repo, args["path"])
        try:
            txt = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            return f"error: {e}"
        old, new = args["old_string"], args["new_string"]
        cnt = txt.count(old)
        if cnt == 0:
            return "error: old_string not found"
        if cnt > 1 and not args.get("replace_all"):
            return f"error: old_string occurs {cnt} times; pass replace_all=true or make it unique"
        txt = txt.replace(old, new) if args.get("replace_all") else txt.replace(old, new, 1)
        open(path, "w", encoding="utf-8").write(txt)
        return f"edited {args['path']}"
    return "unknown tool"


def reset_repo(repo):
    # 只恢复被改过的文件，避免在 DrvFs 上全树 checkout/clean（很慢）
    files = subprocess.run("git -c core.fileMode=false diff --name-only HEAD", shell=True,
                           cwd=repo, capture_output=True, text=True).stdout.split()
    if files:
        subprocess.run(["git", "-c", "core.fileMode=false", "checkout", "HEAD", "--"] + files,
                       cwd=repo, capture_output=True)
    subprocess.run("git clean -qfd -e build -e '*.so'", shell=True, cwd=repo,
                   capture_output=True, timeout=300)


def run_one(iid, steps=30, model=None, base=None, key=None):
    root = workdir(iid)
    repo = os.path.join(root, "repo-clean")
    outdir = os.path.join(root, "agent"); os.makedirs(outdir, exist_ok=True)
    inst = json.load(open(f"/root/swebench/instances/{iid}.json"))
    problem = inst.get("problem_statement") or ""
    hints = inst.get("hints_text") or ""
    if not (base and key):
        base, key, model = load_credentials()
    model = model or "deepseek-chat"

    if os.environ.get("AGENT_DEBUG"):
        print(f"  [{iid}] start reset", flush=True)
    reset_repo(repo)
    open(os.path.join(outdir, "trace.log"), "w").close()
    if os.environ.get("AGENT_DEBUG"):
        print(f"  [{iid}] repo reset", flush=True)
    sys_prompt = SYSTEM
    if os.environ.get("AGENT_FORCE_EDIT"):
        sys_prompt += ("\nIMPORTANT: You MUST call edit_file on the most likely buggy code "
                       "within your first 8 tool calls. Do not spend all steps only reading; "
                       "make a concrete minimal edit, then refine it.\n")
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": f"Repository: {inst['repo']} @ {inst['base_commit'][:10]}\n\n"
                                            f"ISSUE:\n{problem}\n" + (f"\nHINTS:\n{hints}\n" if hints else "")}]
    t0 = time.time(); tool_calls = 0
    usage = {"prompt_tokens": 0, "completion_tokens": 0,
             "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0}
    for step in range(steps):
        t1 = time.time()
        resp = api_call(base, key, model, messages, TOOLS)
        u = _usage_tokens(resp)
        for k in usage:
            usage[k] += u.get(k, 0)
        if os.environ.get("AGENT_DEBUG"):
            print(f"  [{iid}] step {step} api {round(time.time()-t1,1)}s", flush=True)
        msg = resp["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         **({"tool_calls": calls} if calls else {})})
        if not calls:
            if "finish" in (msg.get("content") or ""):
                break
            messages.append({"role": "user", "content": "Continue with a tool call; call finish when the patch is ready."})
            continue
        done = False
        for c in calls:
            fn = c["function"]["name"]; args = c["function"]["arguments"]
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except Exception:
                args = {}
            if fn == "finish":
                return _save(repo, outdir, iid, step, t0, args.get("summary", ""), messages, usage)
            result = apply_tool(repo, fn, args)
            with open(os.path.join(outdir, "trace.log"), "a") as tf:
                tf.write(f"\n### {fn} {json.dumps(args)[:300]}\n{str(result)[:800]}\n")
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": str(result)[:8000]})
            tool_calls += 1
    return _save(repo, outdir, iid, steps, t0, "step limit", messages, usage)


def _save(repo, outdir, iid, steps, t0, summary, messages, usage=None):
    r = subprocess.run("git -c core.fileMode=false diff HEAD", shell=True, cwd=repo,
                       capture_output=True, text=True)
    patch = r.stdout
    subprocess.run("git reset -q", shell=True, cwd=repo, capture_output=True)
    open(os.path.join(outdir, "fix.patch"), "w").write(patch)
    usage = usage or {"prompt_tokens": 0, "completion_tokens": 0,
                      "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0}
    cost = estimate_cost(usage)
    meta = {"iid": iid, "steps": steps, "secs": round(time.time() - t0, 1),
            "summary": summary, "patch_bytes": len(patch),
            "usage": usage, "est_cost_usd": cost}
    json.dump(meta, open(os.path.join(outdir, "meta.json"), "w"))
    return {"iid": iid, "steps": steps, "secs": round(time.time() - t0, 1),
            "patch_bytes": len(patch), "tokens": usage, "est_cost_usd": cost}


def main():
    if sys.argv[1] == "one":
        print(run_one(sys.argv[2]))
        return
    ids = [l.strip() for l in open(sys.argv[2]) if l.strip() and not l.startswith("#")]
    workers = 3; steps = 30; model = "deepseek-chat"
    args = sys.argv[3:]
    i = 0
    while i < len(args):
        if args[i] == "--workers": workers = int(args[i + 1]); i += 2
        elif args[i] == "--steps": steps = int(args[i + 1]); i += 2
        elif args[i] == "--model": model = args[i + 1]; i += 2
        else: i += 1
    base, key, default_model = load_credentials()
    model = model or default_model
    if os.environ.get("AGENT_DEBUG"):
        print(f"main: {len(ids)} ids, workers={workers}", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_one, iid, steps, model, base, key): iid for iid in ids}
        for f in as_completed(futs):
            iid = futs[f]
            try:
                res = f.result(); results.append(res)
                tok = res.get("tokens") or {}
                print(f"DONE {iid} steps={res['steps']} secs={res['secs']} patch={res['patch_bytes']} "
                      f"tok_in={tok.get('prompt_tokens', 0)} tok_out={tok.get('completion_tokens', 0)} "
                      f"cost=${res.get('est_cost_usd', 0)}")
            except Exception as e:
                print(f"ERROR {iid}: {e}")
    tot = {"prompt_tokens": sum((r.get("tokens") or {}).get("prompt_tokens", 0) for r in results),
           "completion_tokens": sum((r.get("tokens") or {}).get("completion_tokens", 0) for r in results),
           "prompt_cache_hit_tokens": sum((r.get("tokens") or {}).get("prompt_cache_hit_tokens", 0) for r in results),
           "prompt_cache_miss_tokens": sum((r.get("tokens") or {}).get("prompt_cache_miss_tokens", 0) for r in results)}
    print(f"TOTAL tokens={json.dumps(tot)} est_cost_usd=${estimate_cost(tot)}")
    json.dump({"results": results, "total_usage": tot, "total_est_cost_usd": estimate_cost(tot)},
              open(os.path.join("/root/swebench", "agent-last-run.json"), "w"))


if __name__ == "__main__":
    main()
