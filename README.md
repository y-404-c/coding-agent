# Coding Agent：上下文工程的受控实验

> 这个项目不是在造一个"能修 bug 的 Agent"。它是一次受控实验：**模型不变、题集不变、评分不变，只改上下文怎么构造**，用同一套 harness 把差异量出来。
> 所有数字均由 `ca report build` 从真实 trace 汇总，**未手工填写**。

## 0. 主结果表（第一屏）

**状态：5 策略 × 40 题 × 3 次 = 600 条真实运行已全部完成，600/600 通过。**

| 策略 | 上下文构造 | pass@1 | pass@2（含自纠正） | 平均 token/题 | 平均步数/题 | 定位 Top-1 |
|---|---|---|---|---|---|---|
| S0 | 整文件全文 | 98.3% | 100.0% | 5267 | 2.9 | 97.5% |
| S1 | AST 切片（目标函数 + 同文件依赖） | 100.0% | 100.0% | 5255 | 3.1 | 98.3% |
| S2 | S1 + 官方测试代码 | 100.0% | 100.0% | 6042 | 3.0 | 99.2% |
| S3 | S2 + 3 个相似题的修复范例 | 98.3% | 100.0% | 8370 | 3.0 | 100.0% |
| S4 | S3 上下文（不变）+ 结构化回喂 | 100.0% | 100.0% | 8079 | 3.0 | 100.0% |

实验条件：模型 `qwen3.8-flash`、temperature=0、QuixBugs 40 题、3 次重复、配置指纹 `9ae68d09d4aa54d1...`。完整数据见 [reports/ab_results.md](reports/ab_results.md) 与 [reports/runs.csv](reports/runs.csv)。

**诚实结论（重要，别讲错）**：pass@1 在 98.3%~100% 之间，已触及天花板，**不存在有意义的正确率增量曲线**。本实验真正稳定的结论在成本维度：S1 用最少 token（5255）达到满分，堆 few-shot 的 S3 多花 59% token（8370）却掉到 98.3%。S4 与 S3 上下文完全相同、仅失败回喂形态不同（结构化 vs 原始），是全实验唯一的单变量对照。不要把"提升"写成卖点——这里验证的是"同等正确率下哪种上下文更省"。

从零复跑：

```bash
ca eval run --strategies S0,S1,S2,S3,S4 --reps 3      # 跑完自动写 reports/ab_results.md
ca report build                                        # 也可从库中重建表
```

## 1. 实验结论要回答什么

| 问题 | 对应消融 |
|---|---|
| 只给目标函数 + 直接依赖，比整文件更好吗？省多少 token？ | S0 → S1 |
| 把"期望行为"（官方测试）放进上下文，能否提升修复率？ | S1 → S2 |
| 3 个同族修复范例有用吗，还是只是多烧 token？ | S2 → S3 |
| 失败后**结构化回喂**能救回多少题？ | S3 → S4 |

提升分解（pass@1 差值）：ast_slice +1.7pp / tests +0.0pp / fewshot -1.7pp / feedback +1.7pp。注意 pass@1 已触及天花板，这些 ±1.7pp 落在 2/120 条的抖动量级内，**不构成可靠的正确率提升结论**；稳定的差异只在 token 成本上。

## 2. 已验证与未验证（诚实状态）

| 项 | 状态 | 证据 |
|---|---|---|
| 单元测试 | 通过 | `python -m pytest -q` → 72 passed |
| 数据集可运行性 | 40/40 | [dataset_smoke.md](reports/dataset_smoke.md)（异常题 0） |
| 全链路（Loop→工具→patch→沙箱→落库） | 通过 | `ca selftest`（脚本化客户端，**不代表真实指标**） |
| FastAPI / Gradio demo | 已实现，本地可跑 | 见第 9 节 |
| 5 策略 × 40 题 × 3 次真实评测 | 已完成 | 600/600，[reports/ab_results.md](reports/ab_results.md) |
| 公网 demo 链接 | **未部署** | 本地启动命令见第 9 节 |

`ca selftest` 的自检输出（bitcount/S2，scripted 客户端，tokens=2382、steps=3）只证明管线连通，**不能当作模型能力数字**。

## 3. 数据集事实与限制

- 来源：QuixBugs（`jkoppel/QuixBugs` master zip），40 道单文件 Python 题，`python_programs/`（buggy）与 `correct_python_programs/`（正确版）一一配对，测试在 `python_testcases/`，用例数据在 `json_testcases/`。
- 测试通过项目自带 `conftest.py` 设置 `pytest.use_correct` 开关，选择导入 buggy 还是 correct 版本；`node.py` 等为共享支撑文件（不作为题目）。
- 实测可运行 40/40。其中 `bitcount`、`find_first_in_sorted` 的 buggy 版本会**死循环**，smoke 结果为 `0/0/0`（超时），按"buggy 应失败"判定为 ok；评测里这类题每次失败尝试会吃满 `sandbox.timeout_s`（默认 10s）。
- **定位指标退化**：QuixBugs 每题是单文件，文件级定位没有区分度。本项目的定位 Top-K 改为**符号级**——Agent 第一次读取/切片命中真实要改的函数（`StepRecord.target_hit`）记为 Top-1 命中，第几步命中记为定位步号。这一点在面试里要主动说明，不要假装是文件级定位。

## 4. 架构

```
CLI (ca) / FastAPI / Gradio
        │
        ▼
ExperimentRunner ── config_hash 去重、repeats、resume、单题异常不中断
        │
        ▼
AgentRunner（手写 ReAct loop，策略无关）
   ├─ ContextBuilder（S0/S1/S2/S3/S4，唯一变量）
   ├─ LoopGuard（同工具同参数连续 2 次）
   ├─ TokenBudget（调用前检查）
   ├─ ActionParser（JSON 结构化输出校验）
   └─ CorrectionPolicy（错误分类 → 回喂 → 2 次上限 → 降级）
        │
        ▼
ToolRegistry：read_file / grep / ast_slice / edit_file / run_test
        │
        ▼
Workspace（内存虚拟文件系统：originals/files/edits，patch 是产物）
        │
        ▼
TestRunner：临时目录 + subprocess pytest + 超时/输出截断 → TestReport
        │
        ▼
SQLite（runs 表：完整 trace_json + 扁平查询字段）
```

设计约束：`edit_file` **不落盘**，只改内存 Workspace 并产出 unified diff；评测时由 `PatchGrader` 把 patch 应用到原始副本上重新跑官方测试，判定物是测试结果，不是模型自称，也不是 patch 文本相似度。

## 5. 上下文策略矩阵

`src/codingagent/context/` 下每个策略一个 builder，实现同一接口 `build(BuildContext, max_chars) -> ContextBundle`。切换策略只改配置/CLI 参数，Agent Loop 一行不动。

| 策略 | 构造方式 | 假设 |
|---|---|---|
| S0 `FullFileContextBuilder` | 整文件 | 朴素做法的上限 |
| S1 `AstSliceContextBuilder` | 标准库 `ast`：目标函数 + 同文件被调用函数（BFS，深度 2）+ imports + 被引用的模块级常量；切片失败回退整文件 | 降噪提准确率、省 token |
| S2 `AstSliceWithTestsContextBuilder` | S1 + 本题官方测试代码 | 正确性信号有用 |
| S3 `FewShotContextBuilder` | S2 + 3 个 few-shot（来自**其他题**的 correct 版本，词袋 Jaccard + 参数个数相似度排序，**排除本题防泄漏**） | 示例能进一步提升 |
| S4 `CorrectiveContextBuilder` | **与 S3 单变量对照：上下文构造完全相同（同 builder / few-shot / prompt），仅失败后回喂形态从 `raw` 换为 `structured`** | 隔离出"回喂形态"的净效果 |

## 6. 手写 Agent Loop 的四道防护

`src/codingagent/agent/loop.py`，无 LangChain 等 Agent 框架：

1. **步数上限** `max_steps=10`（配置项，1..30 校验）。
2. **token 预算** `token_budget=15000`，**每次调用 LLM 前检查**，允许最后一次超额，停止原因记为 `token_budget`。
3. **死循环防护** 同一工具 + 完全相同的参数连续出现 2 次 → `loop_detected`。
4. **结构化输出校验** 只接受单个 JSON 对象；解析失败按 `format_errors` 回喂格式说明，最多 2 次。

另外两条终止条件：`tests_passed`（run_test 通过后提前结束）与 `llm_error`（网络/接口异常，分类为 `env_error`，不重试）。

单次尝试结束后，harness 会**独立复算**：`PatchGrader.grade_workspace()` 重新跑测试，`passed = report.ok and 有实质改动`。空 patch / 等价改动（`noop_patch`）不算通过。

## 7. 五个工具与失败返回

全部实现 `BaseTool.invoke()`，**任何异常都被兜底成结构化 `ToolResult(ok=False, error_category=...)`，不向上抛**。

| 工具 | 作用 | 失败返回 |
|---|---|---|
| `read_file(path)` | 带行号读源码 | 文件不存在 → 列出可用文件 |
| `grep(pattern)` | 正则搜索（非法正则回退按字面量） | 无匹配 → 空结果 + 提示换词 |
| `ast_slice(file, symbol)` | 目标函数 + 直接依赖 | 符号不存在 → 列出文件顶层符号 |
| `edit_file(path, old, new)` | 生成内存 patch + unified diff | `old` 匹配 0 次 → 返回实际内容片段；匹配多处 → 报出出现行号，要求放大上下文 |
| `run_test(test_id)` | 临时目录跑官方测试 | 超时/OOM/导入错误 → 结构化错误类别 |

## 8. 自纠正：分类、回喂、上限、兜底

### 8.1 错误分类（硬编码，`ErrorCategory.retryable`）

| 类别 | 可纠 | 处理 |
|---|---|---|
| `syntax_error` / `name_error` / `import_error` / `runtime_error` | 是 | 回喂重试 |
| `assertion_failure` | 是 | 结构化回喂：哪个测试、期望值、实际值 |
| `empty_patch` / `noop_patch` | 是 | 判定无效修复，要求重做 |
| `timeout` / `memory_error` | 否 | 直接降级（重试只会再烧钱） |
| `env_error` / `unknown` | 否 | 降级 |

### 8.2 回喂形态：S4 与 S0-S3 的唯一差别

> **S4 vs S3 是全实验唯一的单变量对照：上下文构造与 S3 完全相同（同 builder、同 few-shot、同 prompt 骨架），唯一差异是失败后回喂形态从 `raw` 换成 `structured`。因此 S4−S3 的 delta 可以直接归因于"回喂形态"，而不是上下文或其他变量。** 这是回答"你怎么证明是回喂起的作用"的依据。

S0-S3 回喂**原始测试输出**（`raw`，保证是公平对照）。S4 回喂 `structured` JSON：

```json
{
  "error_category": "assertion_failure",
  "failed_test": "test_gcd",
  "expected": "...",
  "actual": "...",
  "relevant_code": "...",
  "related_schema_hint": "...",
  "last_patch": "...",
  "instruction": "..."
}
```

### 8.3 上限与兜底

- 最多重试 **2 次**（`correction.max_retries=2`），不可纠类别 0 次。
- 仍失败时输出降级说明：最接近成功的 patch 草稿 + 置信度说明 + 最后一次不确定的步骤（`AttemptRecord.degrade_note`），**不静默失败、不无限重试**。

## 9. 评测 harness 与指标

### 9.1 判定流程

Agent 产出 patch → 写入临时目录的原始代码副本 → `subprocess` 跑官方 pytest（带超时、输出截断）→ 全绿才算 pass。`ca eval regrade` 可对历史 trace 的 patch 重新独立复算，输出不一致条数（应为 0）。

### 9.2 指标定义

| 指标 | 定义 |
|---|---|
| pass@1 | 第一次尝试（attempt[0]）通过测试的比例 |
| pass@2 | 前两次尝试中任意一次通过的比例（含一次自纠正） |
| 定位 Top-1/Top-K | Agent 首次命中真实目标函数的步号（符号级，见第 3 节说明） |
| token 成本 | 每题总 token（含重试），按策略对比 |
| 失败归因分布 | 每个失败 case 的 `error_category` 占比 |

### 9.3 trace 与失败归因

每次 run 落 SQLite（`var/runs.db`），含：`run_id / strategy / problem_id / repeat_index / steps[]（thought/action/args/observation/tokens/latency_ms/target_hit）/ final_patch / test_report / error_category / passed / stop_reason / degrade_note / config_hash / prompt_version`。失败题不丢弃，基础设施异常也会生成 `env_error` 的 trace 落库。

## 10. 实验纪律

- 同一模型、`temperature=0`、同一 prompt 模板骨架（`prompt_version` 常量）、同一题集、同一评分逻辑。
- 每配置 **3 次复现**（`eval.repeats=3`），指标取均值；抖动处理：报告里同时给出每次复现的单次值与均值，不挑最好的一次。`temperature=0` 仍有抖动（服务端批处理/采样实现），所以复现次数不能省。
- **不因为某策略结果差就单独调它的 prompt**。要改 prompt 就全策略一起改，并保留 `prompt_version` 与 `config_hash`（配置指纹），resume 时按 `(config_hash, strategy, problem_id, repeat_index)` 去重，避免混入不同版本的样本。
- `scripted` 客户端只能用于 `ca selftest`；`ca eval run` 检测到 scripted 会拒绝执行（除非显式 `--allow-scripted`）。
- **bad case 回流**：项目二评测框架可导出 `priority_problems.json`（高频失败题排在前）。下一轮实验加 `--priority path/to/priority_problems.json`，`ca eval run` 会按该顺序优先跑这些题，便于验证修复是否真正降低失败率。只有前后两轮真实报告对比才能声明提升。

## 11. 快速开始

```powershell
# 1) 安装（建议虚拟环境）
cd e:\resume\coding-agent
python -m pip install -e ".[dev]"

# 2) 数据集（自动下载 QuixBugs，校验 40 题配对）
ca dataset fetch
ca dataset verify
ca dataset smoke          # 写 reports/dataset_smoke.md，如实记录异常题

# 3) 跑单测 / 全链路自检（不产生真实指标）
python -m pytest -q
ca selftest

# 4) 配置模型（不要把密钥写进仓库）
$env:CA_LLM__API_KEY = "sk-..."      # 也支持 DEEPSEEK_API_KEY / OPENAI_API_KEY
# 可选：$env:CA_LLM__MODEL = "deepseek-chat"

# 5) 单题调试
ca agent run --problem bitcount --strategy S2 --show-observation

# 6) 正式实验（40 题 × 5 策略 × 3 次复现，自动写 A/B 表）
ca eval run --strategies S0,S1,S2,S3,S4 --reps 3
ca eval regrade            # 独立复算，核对判定一致性
ca report build            # 从库里重建 reports/ab_results.md

# 6b) 按项目二导出的高频失败题优先跑（bad case 回流）
ca eval run --strategies S4 --reps 3 --priority ../agent-eval/data/priority_problems.json

# 7) API / demo
ca serve --port 8000
python -m pip install -e ".[demo]"
python demo/gradio_app.py
```

没有安装包时可用 `$env:PYTHONPATH="src"; python -m codingagent ...` 等价运行。

## 12. 项目结构

```
coding-agent/
├── configs/default.yaml            # 全部实验参数（模型/预算/超时/策略/复现次数）
├── src/codingagent/
│   ├── cli.py                      # ca dataset|agent|eval|report|selftest|serve
│   ├── config.py                   # YAML + CA_* 环境变量、config_hash
│   ├── domain/                     # enums（策略/错误类别）、models（trace/报告）
│   ├── llm/                        # openai_compatible / scripted（仅自检）
│   ├── agent/                      # loop.py 手写 ReAct、prompts、parser、guards
│   ├── tools/                      # read_file / grep / ast_slice / edit_file / run_test
│   ├── context/                    # S0-S4 builders、AST 切片、few-shot 选择
│   ├── correction/                 # 错误分类、raw/structured 回喂、重试策略
│   ├── sandbox/                    # workspace、unified diff、pytest runner、复算
│   ├── dataset/                    # QuixBugs 获取/校验/配对
│   ├── eval/                       # metrics、report、experiment runner、regrade harness
│   ├── storage/repository.py       # SQLite trace 仓储
│   ├── api/                        # FastAPI（不暴露 correct_code）
│   └── …
├── demo/gradio_app.py              # 逐步修复过程可视化
├── tests/                          # 72 个单测：parser/guards/工具/策略/loop/harness
├── data/quixbugs/                  # 40 题数据（fetch 生成）
├── reports/                        # ab_results.md / runs.csv / summary.json / dataset_smoke.md
└── var/runs.db                     # trace 库（selftest 用独立 var/selftest.db）
```

## 13. 红线对照

1. 不编数字：README 与报告里的指标全部来自脚本输出；未跑的一律 `___` / 待实测。
2. 不 clone 现成 Agent 仓库：Agent Loop、工具、harness、报告均为本仓库实现，未使用 LangChain 等 Agent 框架。
3. 不用完整 SWE-bench：主线是 QuixBugs 40 题。
4. 不套黑盒框架：策略只替换 `ContextBuilder`，循环可控可审计。
5. 不静默丢题：单题异常/超时也会落库，smoke 如实列出 `buggy_passes` / `correct_fails`。
6. 不把 pass@1 绝对值当卖点：卖点是同模型下的 **S0 → S4 delta**。

## 14. 验收清单对照

- [x] 手写 Agent Loop：死循环防护、步数上限、token 预算、结构化输出校验
- [x] 5 个工具均有明确失败返回，不抛异常
- [x] S0-S4 可配置切换，Loop 不随策略改动
- [x] 错误分类表 + 结构化回喂 + 2 次上限 + 降级兜底（分层在 `correction/`）
- [x] 独立可复跑 harness，判定物是测试结果（`ca eval regrade`）
- [x] 5 策略 × 40 题 × 3 次真实评测（600/600）
- [x] A/B 表四个指标的真实值（见第 0 节与 `reports/ab_results.md`）
- [x] pass@1 / pass@2 / 定位 / token 成本四项定义（第 9.2 节）
- [x] 失败 case 均有 `error_category` 与完整 trace（SQLite）
- [x] README 第一屏是主结果表
- [ ] demo 公网链接（**未部署**，本地可跑）
