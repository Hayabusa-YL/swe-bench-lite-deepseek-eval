# SWE-bench Lite 全量本地评测记录

日期：2026-11-21 ｜ 环境：Windows 11 + WSL2（Ubuntu2），Python 3.8–3.11，uv 虚拟环境，无 Docker、无 conda
数据集：SWE-bench_Lite（HuggingFace，300 个实例）
被测 agent：DeepSeek 官方 API（deepseek-chat），自定义工具循环（bash / read_file / edit_file / finish）

---

## 0. 结论（按官方判定定义）

| 指标 | 数值 |
|---|---|
| 完成实例 | 300 / 300 |
| **RESOLVED** | **116 / 300 = 38.7%** |
| 基线有效的 287 题中 | 115 / 287 = 40.1% |
| 产出非空补丁的 216 题中 | 116 / 216 = 53.7% |
| 未产出补丁（空补丁） | 84 题 |
| 补丁应用失败 | 1 题（astropy-14182） |
| 测试未能执行（本地依赖问题） | 40 题 |
| 测试真实失败 / P2P 回归 | 48 + 11 题 |
| 基线未复现失败（口径待核） | 13 题 |

判定采用 SWE-bench 官方口径：`resolved = FAIL_TO_PASS 全部通过 且 PASS_TO_PASS 全部未被破坏`（详见第 4 节）。

---

## 1. 执行记录（时间线与成本）

所有阶段的原始日志保留在 WSL 内 `/root/swebench/`，逐题日志在 `/root/swebench/work/<instance_id>/`。

### 1.1 时间线

| 时间（本机，2026-11-21） | 阶段 | 内容 | 耗时 |
|---|---|---|---|
| 13:33 前 | 环境与工具 | 检查 WSL/uv/Python；Docker 引擎不可用，改用 uv 逐题 venv；写 `swe_all.py`、`swe_agent.py`、`swe_parallel.py` | 约 40 分钟 |
| 13:33–13:50 | pilot | 用 requests、pytest 小批量验证 setup/baseline/agent/eval 全链路 | 约 17 分钟 |
| 13:50–14:10 | 环境修正 | 修 pilot 暴露的问题（legacy develop、二进制轮子等），跑通 300 题 setup/baseline | 约 20 分钟 |
| 14:10–15:43 | 第一轮 agent | 300 题各跑一次，最多 30 步 | 1 小时 33 分 |
| 15:41–16:00 | 第一轮评测 | 170 个非空补丁跑 F2P/P2P（当时判定器：F2P 无 FAILED 即算过） | 约 19 分钟 |
| 16:01–16:54 | 第二轮 agent | 128 个空补丁实例重跑，强制“前 8 步必须落笔”，最多 40 步；46 题产出补丁 | 53 分钟 |
| 16:57 | 第二轮评测 | 46 个新补丁评测 | 约 3 分钟 |
| 17:00–18:20 | 口径修正与全量重评 | 修 django 运行器引号/标签问题、matplotlib/pytest 版本桩、pyparsing 版本；skip 语义改为官方口径；216 个补丁全量重跑并判分 | 约 80 分钟 |

**总用时（从建仓到最终结果）约 4 小时 50 分钟**；其中模型推理（agent 阶段）合计约 2 小时 26 分，环境搭建/评测/修 bug 约占一半。

### 1.2 成本

| 项目 | 数值 | 说明 |
|---|---|---|
| API 调用步数 | 第一轮 300 题（≤30 步/题）+ 第二轮 128 题（≤40 步/题）| 第二轮记录精确：4016 步、9342 秒（单实例平均 73 秒） |
| 单步耗时 | 约 7–15 秒（含工具执行） | 取自 `agent-last-run.json` |
| 花费（13:33–18:20） | **¥22.65** | 平台侧计费口径，按小时分桶对齐评测时间窗 |
| 请求数 | 18,852 次 | 同上 |
| token 用量 | 输入 1.98 亿（缓存命中 95.9%），输出 267 万 | 同上 |
| 全天花费（11-21） | ¥25.56 | 含同一 key 当天其他零星调用 |
| 磁盘 | 约 10 GB | 300 个 repo-clean + repo-base 快照、300 个 venv、300 个 eval 副本 |
| 其他 | 无 | 未使用付费算力/镜像/托管服务 |

**口径说明**：agent 脚本当时未累计 API 返回的 usage 字段，无法从本地回溯。事后补数方式：登录 DeepSeek 平台用量页，调用其 `usage/by_api_key/{amount,cost}` 接口，按小时分桶取 2026-11-21 全天数据，再把 13:33–18:20 这六个桶相加。

由此得到三条限制，引用数字时需一并说明：① 这是**平台侧**计费数据，不是脚本自统计；② 同一 API key 在该时间窗内还有本机其他调用的零星消耗，占比很小但未剥离；③ 全天总额 25.56 元比对的是当天所有活动，评测是最大一块但不是全部。输入 token 中 95.9% 命中缓存，是总花费远低于"全按未命中计价"的主因。

后续改进：`swe_agent.py` 已补上统计逻辑——每次 API 调用累计 `usage.prompt_tokens / completion_tokens / prompt_cache_hit_tokens`，逐题写进 `meta.json`，整轮写进 `agent-last-run.json` 的 `total_usage`，并按可配置单价（环境变量 `DS_PRICE_IN_MISS / IN_HIT / OUT`）输出估算费用；该逻辑已用构造数据静态验证，尚未在新一轮实跑。

---

## 2. 方法

每个实例独立完成以下五步：

1. **取材**：从 GitHub codeload 按 `base_commit` 下载 tarball，解出两个快照——`repo-clean`（给 agent 改）与 `repo-base`（评测用，不打补丁）。
2. **环境**：按仓库与版本配置 Python（django 3.8/3.9/3.10，flask 3.11，其余 3.9），`uv` 建独立 venv，安装仓库依赖后 `pip install -e .`（老 setup.py 项目走 pip legacy develop）。
3. **基线**：`repo-base` + `test_patch`，确认 FAIL_TO_PASS 在基线上失败，记录 PASS_TO_PASS 在基线就失败的集合。
4. **agent**：仅作用于 `repo-clean`；工具为 bash（在 repo-clean 内执行）、read_file、edit_file、finish；通过 `git diff` 得到补丁。
5. **评测**：`repo-base` + 模型补丁 + `test_patch`，跑 FAIL_TO_PASS 与 PASS_TO_PASS；与基线失败集合比较，判定 resolved。

评测并行度 4，每实例独立 venv 与独立 eval 副本；agent 并行度 3。

---

## 3. 遇到的问题与处理

| # | 问题 | 表现 | 处理 |
|---|---|---|---|
| 1 | 没有 Docker | `docker` 命令不存在、Desktop 引擎未启动 | 改用 uv venv 逐题自建环境；因此与官方镜像不完全一致 |
| 2 | 老 setup.py 项目不兼容 PEP 660 | django 2.x/3.x、pytest≤6、sphinx≤3、sklearn≤0.24 的 `pip install -e .` 失败 | 走 `pip install --no-use-pep517 -e .` legacy 路径，临时移走 `pyproject.toml` |
| 3 | numpy/pandas 源码编译过慢或失败 | 安装超时 | 固定装二进制轮子（numpy<2 等） |
| 4 | scikit-learn 缺系统库 | `libopenblas` 缺失 | apt 安装依赖并固定 numpy 版本 |
| 5 | astropy 3.x 缺子模块 | tarball 不含 `astropy_helpers` 子模块，import 失败 | 2 题标记 ENV_BLOCKED（astropy-6938、astropy-7746） |
| 6 | 版本桩文件不在 git 里 | matplotlib 报 `cannot import name '_version'`；pytest 报 `No module named '_pytest._version'` | 评测时从 repo-clean 复制 `lib/matplotlib/_version.py`、`src/_pytest/_version.py` 到 eval 副本 |
| 7 | django 测试名含空格/引号 | PASS_TO_PASS 里有 doctest 名 `assertRaisesMessage shouldn't interpret RE special chars.`，经 `/bin/sh -c` 执行被拆坏 | 运行器改为列表参数（不经 shell），并过滤非 `module.Class.test` 形式的 doctest 标签 |
| 8 | F2P skip 被误判为通过 | sklearn 因缺 pandas 跳过 F2P 测试，旧判定器算通过 | 按官方口径改为仅 PASSED（或 XFAIL）算通过，skip 算失败 |
| 9 | 评测环境依赖漂移 | matplotlib 遇 pyparsing 3.x 弃用告警直接报错；sklearn 旧版在 py3.8 下 cloudpickle 报 `TypeError: an integer is required` | pyparsing 固定 `<3`；sklearn 旧版问题无法在现有 Python 版本内解决，相关 40 题测试未能执行 |
| 10 | 补丁应用失败 | astropy-14182 的补丁无法应用 | 标记 EVAL_ERROR，按未解决计 |

---

## 4. 结果

### 4.1 总体

- 完成：300/300
- resolved：116（38.7%）
- 空补丁：84
- 补丁无法应用：1
- 测试未执行（环境依赖）：40
- 测试实际失败（F2P 未过）：48
- P2P 回归：11
- 基线未复现失败（口径待核，不计入“有效”）：13

注：116 个 resolved 中有 1 题（django-13551）落在基线未复现的 13 题里，因此“基线有效”子集为 115/287 = 40.1%。

### 4.2 分仓（仅基线有效实例）

| 仓库 | 有效题数 | RESOLVED | 通过率 | 空补丁 | 测试未执行 |
|---|---|---|---|---|---|
| django/django | 108 | 49 | 45.4% | 35 | 1 |
| sympy/sympy | 77 | 28 | 36.4% | 20 | 0 |
| matplotlib/matplotlib | 23 | 10 | 43.5% | 6 | 5 |
| scikit-learn/scikit-learn | 18 | 9 | 50.0% | 0 | 9 |
| pytest-dev/pytest | 17 | 10 | 58.8% | 2 | 4 |
| sphinx-doc/sphinx | 16 | 2 | 12.5% | 4 | 10 |
| astropy/astropy | 6 | 2 | 33.3% | 3 | 0 |
| pylint-dev/pylint | 6 | 0 | 0.0% | 3 | 3 |
| pydata/xarray | 5 | 1 | 20.0% | 2 | 2 |
| mwaskom/seaborn | 4 | 0 | 0.0% | 2 | 1 |
| psf/requests | 4 | 2 | 50.0% | 1 | 0 |
| pallets/flask | 3 | 2 | 66.7% | 0 | 1 |

### 4.3 结果分类含义

- **resolved**：F2P 全过、P2P 无基线外回归。
- **空补丁**：agent 未修改任何文件；官方口径下 patch 为 None 即未解决。
- **补丁应用失败**：无法进入测试阶段。
- **测试未执行**：补丁已生成，但本地环境导致测试收集/导入失败（sklearn 旧版、sphinx 依赖、部分 matplotlib），无法判定。
- **测试实际失败**：测试跑了但 F2P 未过。
- **P2P 回归**：F2P 可能通过，但破坏了基线通过的测试。

---

## 5. 与官方口径的对齐情况

**官方判定定义**（`swebench/harness/grading.py`）：实例 resolved 当且仅当 F2P 全部 PASSED/XFAIL（skip 视为失败）且 P2P 全部 PASSED/XFAIL/SKIP（缺失视为失败）。补丁为 None 或应用失败一律未解决。

**本报告采用的规则**：

- F2P：逐个测试名要求出现 PASSED（XFAIL 未单独追踪，按不通过处理，比官方更严格）；
- P2P：以“最终失败集合 − 基线失败集合”判断回归（与官方在“基线失败已排除”这一点上等价；但测试缺失不算失败，比官方更宽松）；
- 补丁为 None / 应用失败：未解决，与官方一致。

**与官方 harness 的差异**（这些都是本地复现不可避免的口径差）：

| 维度 | 官方 | 本次 |
|---|---|---|
| 运行环境 | 每题官方 Docker 镜像，依赖版本固定 | uv venv 自建环境，依赖版本按仓库版本手工钉住 |
| 测试执行 | 官方 per-repo 运行脚本 | pytest（统一 `-rA`）/ django `runtests.py` / sympy 测试文件 |
| 日志解析 | 官方 per-repo parser，带“套件确实运行过”的校验 | 通用解析：pytest 状态行 + django 摘要 |
| agent | 官方 agent 预设（观测/工具格式固定） | 自定义 bash/read/edit 循环，DeepSeek API |
| 数据 spec | v5 从 `swe-bench-tasks` 取 test spec | HuggingFace 数据集自带 F2P/P2P 字段 |

**能否用官方 harness 复算**：本机 Docker 不可用，未能在官方 harness 上复跑；已按官方格式生成 `predictions.jsonl`（instance_id / model_name_or_path / model_patch），在可用 Docker 的机器上执行 `swebench eval`（或 sb-cli/Modal）即可得到严格官方口径的数字。

因此：**116/300 是“按官方判定定义、在本地自建环境”的结果，不应直接与 leaderboard 排名对比**；其中 40 题测试未能执行、13 题基线未复现，是本地环境与官方镜像差异导致的口径不确定性。

---

## 6. 额外想法（个人观察，非评测结论）

- 84 个空补丁仍是最大的不确定性来源：模型读完代码没有落笔就结束。第二轮加“强制先写”后，46 个补丁里有 31 个被旧判定器判 resolved，说明这条约束有效；但 84 题仍未覆盖。
- 40 题“测试未执行”集中在 sklearn（旧版依赖）、sphinx（docutils/jinja 版本），属于环境问题而非模型能力，官方镜像下这些题大概率能测出结果。
- 有 26 题 F2P 只差 1–2 个测试（旧判定器下的统计），定向重试性价比最高。
- 13 题基线未复现失败中，多题是 doctest 形式的 F2P/P2P 标签无法在本地选中，这部分与官方差异最大，建议优先用官方 harness 复核。
- 按仓库看，sympy 题量占比最大（77/300）、通过率 36.4%，其失败模式与 numpy/sympy 版本相关性值得单独分析。

---

## 7. 产物

- 逐题结果：`tmp/swe-bench-lite-final.csv`
- 官方格式预测：`tmp/predictions.jsonl`
- 逐题 agent 轨迹与补丁：WSL `/root/swebench/work/<instance_id>/agent/trace.log`、`agent/fix.patch`
- 评测日志：`/root/swebench/work/<instance_id>/final_f2p.log`、`final_p2p.log`、`parallel_eval.log`
