#!/usr/bin/env python3
"""SWE-bench Lite full-set harness (no Docker).

Subcommands:
  python3 swe_all.py setup <ID> [--py X]
  python3 swe_all.py baseline <ID>
  python3 swe_all.py eval <ID> [patch]
  python3 swe_all.py diff <ID>
  python3 swe_all.py env <ID>        # print resolved config
  python3 swe_all.py list [--repo R] [--state X]
"""
import json, os, shutil, subprocess, sys, glob, re, shlex, time

OLD_ROOT = os.environ.get("SWE_WORK_OLD", "")
WIN_ROOT = "/root/swebench/work"
REPO_CACHE = "/root/swebench/repos"
INST_DIR = "/root/swebench/instances"
VENV_DIR = "/root/swebench/venvs"
EVAL_DIR = "/root/swebench/eval"
TP_DIR = "/root/swebench/tp"

MIRRORS = {
    "pytest-dev/pytest": "pytest",
    "sphinx-doc/sphinx": "sphinx",
    "mwaskom/seaborn": "seaborn",
    "pallets/flask": "flask",
    "astropy/astropy": "astropy",
    "django/django": "django",
    "matplotlib/matplotlib": "matplotlib",
    "pylint-dev/pylint": "pylint",
    "psf/requests": "requests",
    "scikit-learn/scikit-learn": "scikit-learn",
    "sympy/sympy": "sympy",
    "pydata/xarray": "xarray",
}
GH_PROXIES = ["https://gh-proxy.com/https://github.com",
              "https://ghfast.top/https://github.com",
              "https://ghproxy.net/https://github.com"]

# per-repo python / deps / post-install
def repo_cfg(i):
    repo = i["repo"]
    raw = (i.get("version") or "0").split(".")
    major = int(raw[0]) if raw[0].isdigit() else 0
    minor = int(raw[1]) if len(raw) > 1 and raw[1].isdigit() else 0
    cfg = {"py": "3.9", "deps": ["pytest"], "env": {}, "runner": "pytest",
           "install_extra": [], "legacy": False}
    if repo == "django/django":
        cfg["py"] = "3.10" if major >= 5 else ("3.9" if major >= 4 else "3.8")
        cfg["runner"] = "django"
        cfg["deps"] = []
    elif repo == "pytest-dev/pytest":
        cfg["py"] = "3.9"
        if major <= 6:
            cfg["legacy"] = True
            cfg["deps"] = ["pytest", "attrs", "more-itertools", "pluggy<1", "packaging", "py",
                           "setuptools==59.5.0", "wheel==0.37.1", "iniconfig", "pip==23.3.2",
                           "setuptools_scm==6.4.2"]
        else:
            cfg["deps"] = ["pytest", "attrs", "iniconfig", "packaging", "pluggy<2"]
    elif repo == "sphinx-doc/sphinx":
        cfg["py"] = "3.9"
        if major >= 7:
            cfg["deps"] = ["pytest", "docutils>=0.20,<0.22", "jinja2>=3.1", "markupsafe>=2.1",
                           "alabaster>=0.7", "babel", "snowballstemmer", "imagesize",
                           "requests", "packaging", "pygments>=2.14", "setuptools"]
        else:
            deps = ["pytest", "docutils<0.17", "jinja2<3.1", "markupsafe<2.1", "alabaster<0.8",
                    "babel", "snowballstemmer", "imagesize", "requests", "packaging", "pygments"]
            if major <= 3:
                cfg["legacy"] = True
                deps += ["setuptools==59.5.0", "wheel==0.37.1", "pip==23.3.2"]
            cfg["deps"] = deps
    elif repo == "mwaskom/seaborn":
        cfg["py"] = "3.9"
        cfg["deps"] = ["pytest", "numpy<2", "pandas<3"]
    elif repo == "pallets/flask":
        cfg["py"] = "3.11"
        cfg["deps"] = ["pytest", "werkzeug<3", "jinja2<4", "itsdangerous", "click<9", "blinker"]
    elif repo == "psf/requests":
        cfg["py"] = "3.9"
        cfg["deps"] = ["pytest", "chardet", "idna<4", "urllib3<2", "certifi"]
    elif repo == "pylint-dev/pylint":
        cfg["py"] = "3.9"
        cfg["editable"] = False
        if major < 3:
            cfg["deps"] = ["pytest", "astroid", "isort<6", "mccabe", "toml", "platformdirs",
                           "dill", "tomli", "setuptools>=61", "wheel"]
        else:
            cfg["deps"] = ["pytest", "astroid<3", "isort<6", "mccabe", "toml", "platformdirs", "dill", "tomli"]
    elif repo == "sympy/sympy":
        cfg["py"] = "3.9"
        cfg["runner"] = "sympy"
        cfg["deps"] = ["pytest", "mpmath", "setuptools"]
    elif repo == "matplotlib/matplotlib":
        cfg["py"] = "3.9"
        cfg["importlib"] = True
        cfg["deps"] = ["pytest", "numpy<2", "setuptools_scm", "wheel"]
        cfg["env"] = {"MPLBACKEND": "Agg"}
        cfg["install_extra"] = ["setuptools_scm", "numpy<2", "contourpy", "cycler", "fonttools",
                                "kiwisolver", "packaging", "pillow", "pyparsing<3", "python-dateutil"]
    elif repo == "scikit-learn/scikit-learn":
        cfg["legacy"] = True
        if major == 0 and minor <= 24:
            cfg["py"] = "3.8"
            cfg["deps"] = ["pytest", "cython==0.29.15", "numpy==1.17.3", "scipy==1.3.2",
                           "joblib==0.14.1", "threadpoolctl==2.1.0",
                           "setuptools==49.6.0", "wheel==0.34.2", "pip==23.3.2"]
        else:
            cfg["py"] = "3.9"
            cfg["deps"] = ["pytest", "cython<3", "numpy<1.24", "scipy<1.11", "joblib",
                           "threadpoolctl", "setuptools==59.5.0", "wheel==0.37.1", "pip==23.3.2"]
        cfg["env"] = {"SKLEARN_NO_OPENMP": "1"}
    elif repo == "astropy/astropy":
        cfg["py"] = "3.9"
        cfg["legacy"] = True
        # gcc 15 默认 C23，nullptr 成了关键字；astropy 5 的 cextern/cfitsio 仍拿它当变量名
        cflags = ("-std=gnu11 -Wno-error -Wno-error=implicit-function-declaration "
                  "-Wno-error=incompatible-pointer-types -Wno-error=int-conversion -Wno-error=implicit-int")
        cfg["deps"] = ["pytest", "numpy<2", "pyerfa<2.0.2", "extension-helpers>=0.9",
                       "setuptools==59.5.0", "wheel==0.37.1", "cython<3", "packaging<22",
                       "pyyaml", "scipy<1.11", "pip==23.3.2", "hypothesis",
                       "pytest-doctestplus"]
        cfg["env"] = {"ASTROPY_USE_SYSTEM_PYTEST": "1", "CFLAGS": cflags, "CXXFLAGS": "-std=gnu++11"}
        cfg["version_stub"] = "astropy/_version.py"
    elif repo == "pydata/xarray":
        cfg["py"] = "3.9"
        if major == 0 and minor <= 16:
            cfg["legacy"] = True
            cfg["deps"] = ["pytest", "numpy<1.22", "pandas<1.2", "setuptools==59.5.0",
                           "wheel==0.37.1", "hypothesis", "setuptools_scm==6.4.2", "pip==23.3.2"]
        else:
            cfg["deps"] = ["pytest", "hypothesis", "numpy<2", "pandas<3", "packaging"]
    return cfg


def load(iid):
    return json.load(open(os.path.join(INST_DIR, iid + ".json")))


def paths(iid):
    w = os.path.join(WIN_ROOT, iid)
    old = os.path.join(OLD_ROOT, iid)
    if os.path.isdir(old) and not os.path.isdir(w):
        w = old
    return {"root": w, "clean": os.path.join(w, "repo-clean"),
            "base": os.path.join(w, "repo-base"),
            "eval": os.path.join(EVAL_DIR, iid),
            "tp": os.path.join(TP_DIR, iid + ".patch"),
            "venv": os.path.join(VENV_DIR, iid),
            "py": os.path.join(VENV_DIR, iid, "bin", "python")}


def meta_ver(p):
    try:
        lines = open(os.path.join(p["root"], "meta.txt")).read().split("\n")
        return lines[1] if len(lines) > 1 else "0.0.0"
    except OSError:
        return "0.0.0"


def f2p(i):
    v = i["FAIL_TO_PASS"]
    return v if isinstance(v, list) else json.loads(v)


def p2p(i):
    v = i["PASS_TO_PASS"]
    return v if isinstance(v, list) else json.loads(v)


def sh(cmd, cwd=None, env=None, check=False, timeout=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: {cmd}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def run_retry(cmd, attempts=3, **kw):
    """包一层重试：网络抖动（pypi/gitee 偶发卡死）时不至于整题失败。"""
    last = None
    for k in range(attempts):
        try:
            last = sh(cmd, **kw)
        except subprocess.TimeoutExpired as e:
            last = e
            continue
        if last.returncode == 0:
            return last
        time.sleep(5)
    return last


def setup(iid, force_py=None):
    i = load(iid); p = paths(iid); cfg = repo_cfg(i)
    py = force_py or cfg["py"]
    os.makedirs(p["root"], exist_ok=True)
    if not os.path.exists(os.path.join(p["clean"], ".git")):
        ver = i.get("version") or "0.0.0"
        stage = os.path.join(REPO_CACHE, f".stage-{iid}-{os.getpid()}")
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage)
        # 直接抓 base commit 的 tarball（几百 KB~几十 MB，秒级），
        # 免去动辄几分钟且会卡死的 git 全量克隆。
        tgz = stage + ".tgz"
        got = False
        for pb in ["https://gh-proxy.com/https://codeload.github.com",
                   "https://codeload.github.com", "https://ghproxy.net/https://codeload.github.com"]:
            url = f"{pb}/{i['repo']}/tar.gz/{i['base_commit']}"
            for attempt in range(2):
                r = sh(f"curl -fsSL --max-time 600 -o {tgz} '{url}'", timeout=660)
                if r.returncode == 0 and os.path.exists(tgz) and os.path.getsize(tgz) > 50000:
                    got = True
                    break
                time.sleep(3)
            if got:
                break
        if not got:
            raise SystemExit(f"cannot download tarball for {iid}")
        r = sh(f"tar -xzf {tgz} --strip-components=1 -C {stage}", timeout=600)
        if r.returncode != 0 or not os.listdir(stage):
            raise SystemExit("tarball extract failed: " + r.stderr[-800:])
        os.remove(tgz)
        sh("git init -q && git add -A && git -c user.email=a@b -c user.name=a commit -qm base",
           cwd=stage, check=True, timeout=1800)
        os.makedirs(p["clean"], exist_ok=True)
        if not os.path.exists(p["base"]):
            shutil.copytree(stage, p["base"], symlinks=True)
        sh(f"cp -a {stage}/. {p['clean']}/", check=True, timeout=1800)
        shutil.rmtree(stage, ignore_errors=True)
        open(os.path.join(p["root"], "meta.txt"), "w").write(f"{i['repo']}\n{ver}\n")
    if not os.path.exists(p["base"]):
        shutil.copytree(p["clean"], p["base"], symlinks=True)
        sh(f"git -C {p['base']} checkout -q -- . && git -C {p['base']} clean -qfd", check=True, timeout=300)
    # DrvFs 上所有文件看起来都是 755，关掉 fileMode，否则 diff 全是 mode 变化
    for d in (p["clean"], p["base"]):
        if os.path.isdir(os.path.join(d, ".git")):
            sh(f"git -C {d} config core.fileMode false")
            sh(f"git -C {d} config core.autocrlf false")
    if not os.path.exists(os.path.join(p["root"], "meta.txt")):
        open(os.path.join(p["root"], "meta.txt"), "w").write(f"{i['repo']}\n{i.get('version') or '0.0.0'}\n")
    meta = open(os.path.join(p["root"], "meta.txt")).read().split("\n")
    ver = meta[1] if len(meta) > 1 else "0.0.0"
    if not os.path.exists(p["py"]):
        sh(f"uv venv -q --python {py} {p['venv']}", check=True, timeout=900)
    env = dict(os.environ, SETUPTOOLS_SCM_PRETEND_VERSION=ver, **cfg["env"])
    # 系统 gcc 15 会把旧 Cython C 代码里的告警当错误，统一降级
    env.setdefault("CFLAGS", "-Wno-error -Wno-error=implicit-function-declaration "
                             "-Wno-error=incompatible-pointer-types -Wno-error=int-conversion "
                             "-Wno-error=implicit-int")
    env.setdefault("CXXFLAGS", env["CFLAGS"])
    if cfg["install_extra"]:
        r = run_retry(f"uv pip install -q --python {p['py']} " + " ".join(shlex.quote(x) for x in cfg["install_extra"]), env=env, timeout=1800)
        if r.returncode != 0:
            print("EXTRA INSTALL FAILED:", r.stderr[-2000:]); raise SystemExit(1)
    # deps first, then editable
    if cfg["deps"]:
        r = run_retry(f"uv pip install -q --python {p['py']} " + " ".join(shlex.quote(x) for x in cfg["deps"]), env=env, timeout=1800)
        if r.returncode != 0:
            print("DEPS INSTALL FAILED:", r.stderr[-2000:]); raise SystemExit(1)
    if cfg.get("legacy"):
        # 老 setup.py 项目：pip 的 legacy develop 路径比 uv 的 PEP660 更兼容。
        # 若仓库带 pyproject.toml，先临时挪开，否则 pip 拒绝 --no-use-pep517。
        shutil.rmtree(os.path.join(p["clean"], ".eggs"), ignore_errors=True)
        pyproj = os.path.join(p["clean"], "pyproject.toml")
        moved = False
        if os.path.exists(pyproj):
            os.rename(pyproj, pyproj + ".dshbak")
            moved = True
        cmd = f"{p['py']} -m pip install -q --no-build-isolation --no-use-pep517 -e ."
        try:
            r = run_retry(cmd, cwd=p["clean"], env=env, timeout=3600)
        finally:
            if moved:
                os.rename(pyproj + ".dshbak", pyproj)
    else:
        edit = "-e " if cfg.get("editable", True) else ""
        cmd = f"uv pip install -q --python {p['py']} {edit}."
        r = run_retry(cmd, cwd=p["clean"], env=env, timeout=3600)
    if r.returncode != 0:
        print("INSTALL FAILED:", r.stdout[-2000:], r.stderr[-3000:]); raise SystemExit(1)
    open(os.path.join(p["root"], "setup.ok"), "w").write(py)
    print("env ready:", p["venv"], py)


def make_eval(iid, apply_patch=None):
    i = load(iid); p = paths(iid); cfg = repo_cfg(i)
    if apply_patch:
        apply_patch = os.path.abspath(apply_patch)
    os.makedirs(EVAL_DIR, exist_ok=True); os.makedirs(TP_DIR, exist_ok=True)
    shutil.rmtree(p["eval"], ignore_errors=True)
    shutil.copytree(p["base"] if os.path.exists(p["base"]) else p["clean"], p["eval"], symlinks=True)
    if os.path.isdir(os.path.join(p["eval"], ".git")):
        sh(f"git -C {p['eval']} config core.fileMode false")
        sh(f"git -C {p['eval']} config core.autocrlf false")
    # 构建期生成、不在 git 里的文件（版本桩、inplace .so/.pyd）
    if os.path.exists(p["clean"]):
        for rel in ("src/_pytest/_version.py", "lib/matplotlib/_version.py"):
            src = os.path.join(p["clean"], rel)
            if os.path.exists(src):
                dst = os.path.join(p["eval"], rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        r = sh(f"git -C {p['clean']} status --porcelain --ignored -uall")
        for line in r.stdout.splitlines():
            if len(line) < 4:
                continue
            rel = line[3:].strip().strip('"')
            if not (rel.endswith(".so") or rel.endswith(".pyd")):
                continue
            src = os.path.join(p["clean"], rel)
            dst = os.path.join(p["eval"], rel)
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
    if cfg.get("version_stub"):
        stub = os.path.join(p["eval"], cfg["version_stub"])
        os.makedirs(os.path.dirname(stub), exist_ok=True)
        open(stub, "w").write('version = "%s"\nversion_tuple = (%s,)\n'
                              % (meta_ver(p), ", ".join("0" for _ in meta_ver(p).split("."))))
    if apply_patch:
        r = sh(f"git apply --allow-empty -p1 {apply_patch}", cwd=p["eval"])
        if r.returncode != 0:
            r = sh(f"patch -p1 --forward < {apply_patch}", cwd=p["eval"])
            if r.returncode != 0:
                raise SystemExit("patch apply failed: " + r.stdout[-800:] + r.stderr[-800:])
    open(p["tp"], "w").write(i["test_patch"])
    r = sh(f"git apply --allow-empty {p['tp']}", cwd=p["eval"])
    if r.returncode != 0:
        r = sh(f"patch -p1 --forward < {p['tp']}", cwd=p["eval"])
        if r.returncode != 0:
            raise SystemExit("test patch apply failed: " + r.stdout[-800:] + r.stderr[-800:])
    return p


def _run_pytest(iid, ids, tag):
    p = paths(iid); cfg = repo_cfg(load(iid))
    out = os.path.join(p["root"], f"{tag}.log")
    idsfile = os.path.join(p["root"], f"{tag}.ids.json")
    runner = os.path.join(p["root"], ".runner.py")
    open(idsfile, "w").write(json.dumps(ids))
    open(runner, "w").write(
        "import json,os,sys,pytest\nids=json.load(open(sys.argv[1]))\n"
        "extra=['--import-mode=importlib'] if os.environ.get('PYTEST_IMPORTLIB') else []\n"
        "sys.exit(pytest.main(ids+['-q','-p','no:cacheprovider','--tb=no','-rA']+extra))\n")
    env = dict(os.environ, **cfg["env"])
    env["SETUPTOOLS_SCM_PRETEND_VERSION"] = meta_ver(p)
    env["PYTHONPATH"] = os.pathsep.join([p["eval"] + "/src", p["eval"],
                                         env.get("PYTHONPATH", "")])
    extra = []
    if cfg.get("importlib"):
        env["PYTEST_IMPORTLIB"] = "1"
    r = sh([p['py'], runner, idsfile], cwd=p['eval'], env=env, timeout=10800)
    txt = r.stdout + r.stderr
    open(out, "w").write(txt)
    failed = set()
    for line in txt.splitlines():
        s = line.strip()
        m = re.match(r"^(FAILED|ERROR|PASSED)\s+(.*)$", s)
        if m:
            if m.group(1) == "PASSED":
                continue
            rest = m.group(2).split(" - ")[0].strip()
            failed.add(rest)
            continue
        m = re.match(r"^(FAILED|ERROR):?\s+(.*)$", s)
        if m:
            rest = m.group(2)
            if rest.startswith("not found:"):
                rest = rest[len("not found:"):].strip()
            rest = rest.split(" (")[0].strip()
            if rest.startswith(p["eval"] + "/"):
                rest = rest[len(p["eval"]) + 1:]
            failed.add(rest)
    print(f"[{tag}] rc={r.returncode} failed={len(failed)}")
    for f in sorted(failed)[:12]:
        print("   FAIL", f)
    return r.returncode, failed


def _run_django(iid, specs, tag):
    p = paths(iid); out = os.path.join(p["root"], f"{tag}.log")
    labels = []
    for s in specs:
        m = re.match(r"^(\S+) \((.*)\)$", s)
        if m:
            # "test_x (a.b.Class)" -> "a.b.Class.test_x"
            labels.append(m.group(2) + "." + m.group(1))
        else:
            labels.append(s)
    # doctest 名称带空格/引号，django 运行器按模块路径解释会直接报错，过滤掉
    labels = [l for l in labels if re.fullmatch(r"[A-Za-z_][\w.]*", l)]
    env = dict(os.environ)
    env["SETUPTOOLS_SCM_PRETEND_VERSION"] = meta_ver(p)
    env["PYTHONPATH"] = os.pathsep.join([p["eval"], env.get("PYTHONPATH", "")])
    cmd = [p['py'], 'tests/runtests.py', '--settings=test_sqlite', '--parallel', '1', '-v', '1'] + labels
    r = sh(cmd, cwd=p['eval'], env=env, timeout=14400)
    txt = r.stdout + r.stderr
    open(out, "w").write(txt)
    failed = set()
    for line in txt.splitlines():
        m = re.match(r"^(FAIL|ERROR):\s+(\S+) \(([^)]+)\)", line.strip())
        if m:
            failed.add(m.group(2) + " (" + m.group(3).strip() + ")")
    print(f"[{tag}] rc={r.returncode} failed={len(failed)}")
    for f in sorted(failed)[:12]:
        print("   FAIL", f)
    return r.returncode, failed


def _run_sympy(iid, names, tag):
    p = paths(iid); i = load(iid)
    out = os.path.join(p["root"], f"{tag}.log")
    files = re.findall(r"^\+\+\+ b/(.*)$", i["test_patch"], re.M)
    files = [f for f in files if "/tests/test_" in f or f.startswith("test_")]
    # map names -> ids; fall back to whole test files
    ids = []
    for f in files:
        if not os.path.exists(os.path.join(p["eval"], f)):
            continue
        ids.append(f)
    env = dict(os.environ)
    env["SETUPTOOLS_SCM_PRETEND_VERSION"] = meta_ver(p)
    env["PYTHONPATH"] = os.pathsep.join([p["eval"], env.get("PYTHONPATH", "")])
    runner = p["root"] + "/.runner_sympy.py"
    open(runner, "w").write(
        "import sys,pytest\n"
        "rc=pytest.main(sys.argv[1:]+['-q','-p','no:cacheprovider','--tb=no','-rA'])\n"
        "sys.exit(rc)\n")
    r = sh([p['py'], runner] + ids, cwd=p['eval'], env=env, timeout=14400)
    txt = r.stdout + r.stderr
    open(out, "w").write(txt)
    failed = set()
    passed = set()
    for line in txt.splitlines():
        s = line.strip()
        m = re.match(r"^(FAILED|ERROR|PASSED)\s+(\S+?)::(\w+)", s)
        if m:
            (failed if m.group(1) != "PASSED" else passed).add(m.group(3))
    # also bare summary counts
    print(f"[{tag}] rc={r.returncode} failed={len(failed)} passed={len(passed)}")
    for f in sorted(failed)[:12]:
        print("   FAIL", f)
    return r.returncode, failed


def run_tests(iid, ids, tag):
    i = load(iid); cfg = repo_cfg(i)
    if cfg["runner"] == "django":
        return _run_django(iid, ids, tag)
    if cfg["runner"] == "sympy":
        return _run_sympy(iid, ids, tag)
    return _run_pytest(iid, ids, tag)


def baseline(iid):
    make_eval(iid); i = load(iid); p = paths(iid)
    rc1, f1 = run_tests(iid, f2p(i), "baseline_f2p")
    rc2, f2 = run_tests(iid, p2p(i), "baseline_p2p")
    json.dump(sorted(f2), open(os.path.join(p["root"], "baseline_p2p_failed.json"), "w"))
    json.dump({"f2p_rc": rc1, "f2p_failed": sorted(f1)}, open(os.path.join(p["root"], "baseline_f2p_failed.json"), "w"))
    print("baseline: F2P rc:", rc1, "| F2P failed:", len(f1), "| P2P failed:", len(f2))


def evaluate(iid, patch=None):
    make_eval(iid, patch); p = paths(iid); i = load(iid)
    rc1, f1 = run_tests(iid, f2p(i), "final_f2p")
    rc2, f2 = run_tests(iid, p2p(i), "final_p2p")
    bf = os.path.join(p["root"], "baseline_p2p_failed.json")
    base_fail = set(json.load(open(bf))) if os.path.exists(bf) else set()
    regressions = f2 - base_fail
    ok = (not f1 and not regressions)
    print(f"RESULT {iid}: F2P={'PASS' if not f1 else 'FAIL'} P2P={'PASS' if not regressions else 'FAIL'} "
          f"(excluded {len(base_fail)}) => {'RESOLVED' if ok else 'NOT_RESOLVED'}")
    if regressions:
        print("  regressions:", sorted(regressions)[:10])
    return ok


def diff(iid):
    p = paths(iid); out = os.path.join(p["root"], "fix.patch")
    sh(f"git -C {p['clean']} add -A", check=True)
    r = sh(f"git -C {p['clean']} diff --cached HEAD", check=True)
    open(out, "w").write(r.stdout)
    print("patch written:", out, len(r.stdout), "bytes")


def status():
    for j in sorted(glob.glob(os.path.join(INST_DIR, "*.json"))):
        iid = os.path.basename(j)[:-5]; p = paths(iid)
        st = []
        if os.path.exists(os.path.join(p["root"], "setup.ok")): st.append("env")
        if os.path.exists(os.path.join(p["root"], "baseline_f2p.log")): st.append("base")
        if os.path.exists(os.path.join(p["root"], "final_f2p.log")): st.append("eval")
        if os.path.exists(os.path.join(p["root"], "fix.patch")): st.append("fix")
        print(iid, ",".join(st) or "-")


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "setup":
        setup(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif c == "baseline":
        baseline(sys.argv[2])
    elif c == "eval":
        evaluate(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif c == "diff":
        diff(sys.argv[2])
    elif c == "env":
        print(json.dumps(repo_cfg(load(sys.argv[2])), indent=2))
    elif c == "status":
        status()
    else:
        print(__doc__)
