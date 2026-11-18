# 上下文策略 A/B 结果

> 所有数字由 `ca report build` 从 SQLite trace 汇总，**未手工填写**。没有数据的单元格显示 `___`。

| 策略 | pass@1 | pass@2（含自纠正） | 平均 token | 平均步数 | 定位 Top-1 | 定位 Top-3 | 运行数 |
|---|---|---|---|---|---|---|---|
| S2 | 0.0% | 0.0% | 0 | 0.0 | ___ | ___ | 5 |

## 提升分解（pass@1 差值）

- ast_slice：___
- tests：___
- fewshot：___
- feedback：___

## 失败归因

| 策略 | 失败归因分布 | 降级运行数 |
|---|---|---|
| S2 | env_error=5 | 5 |

## 实验条件

- 模型：`deepseek-chat`，temperature=0，prompt 版本 `2026-11-17.1`
- 题集：QuixBugs Python 版，40 题
- 复现次数：1（每格为均值；抖动处理见 README「评测纪律」）
- 配置指纹：`3b31f116dd89814e...`（trace 表可按此过滤）
- 数据源：`E:\resume\coding-agent\var\runs.db`
- 生成时间：2026-11-19 00:52:32 中国标准时间
- 备注：5 条 trace
