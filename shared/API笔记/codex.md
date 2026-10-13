# `codex exec` —— 沙盒里跑 OpenAI Codex CLI 当第二双眼睛

> 建于 2026-09-17（规则 18：探过的 API 当场记档，下次先读这份，不许重探）。
> 两次实战：一份长文档的整体审 + 一次批量裁决。

## 一条命令

```bash
codex exec --skip-git-repo-check --sandbox workspace-write -C "$PWD" "$(cat prompt.txt)" </dev/null > codex-run.log 2>&1
```

## 🔴 两个坑（都会让它「看起来挂了」）

| 坑 | 症状 | 解 |
|---|---|---|
| **目录不是 git 仓库** | 直接拒跑 | 加 `--skip-git-repo-check`，或在隔离副本里 `git init` |
| **stdin 不是 TTY** | 一直等输入，永远不返回 | **必须** `</dev/null` |

⚠️ 日志里的 `failed to renew cache TTL: missing field supports_parallel_tool_calls` 是**无害告警**，不用管。

## 怎么摆材料

在 scratchpad 里开隔离目录（**不要让它直接跑在项目仓库上**），`git init`，把要审的东西拷进去：
正文 `.tex` · 表 · 附录 · 图的 PDF · spec/FAQ 文本 · 渲染出的纯文本 · 正文每页 PNG ·
**一份 `README.txt` 写「已知且刻意，不许报」清单**（不写这个它会把你故意的设计当 bug 报一堆）。

## 耗时与额度

| 任务 | 耗时 |
|---|---|
| 一次 holistic 整体审（读全套材料出报告） | **≈12–13 min** |
| 一次批量裁决（读一个 `findings.json` 输出 verdicts） | **≈10 min** |

🔒 **Codex 额度有限** → **Codex 只跑 1–2 次**，多路并发的活交给 Sonnet subagent，
Codex 用在「一次性读全局、裁掉 Sonnet 的内部矛盾」这种它最值钱的地方。

## 🔑 互审骨架（09-17 实测有效）

```
13 个 Sonnet finder（按维度并行）  ∥  1 次 Codex holistic
            ↓ merge
每条 finding：2 个 Sonnet 判官(facts/marker)  ∥  Codex 一次性批量裁决
Codex 自己的 finding：反过来由 3 个 Sonnet 判官投票
            ↓ synth
```

**这套真正兑现过两次**：
- Sonnet 两个判官一致同意的改法，被 Codex 裁出内部矛盾（数字自相打架）
- 同一个错标**两路独立找到** → 信号最强的一条

workflow 脚本自己写（agent 数 > 10 先问他，见主 `CLAUDE.md`「大规模开 subagent」节）；
每个 agent 先落盘再返回的做法见 `shared/工具手册.md`。

## 在 `/审查` 里怎么用

`/审查` 的 workflow 里有一个可选的 `xmodel-runner`（`args.xmodel: true`），它照上面的配方：
隔离副本 + `git init` + README（已知且刻意 / 已修 / settled）→ `codex exec … </dev/null` → 把 Codex 的发现转成和 Sonnet finder 同一个 schema。

- 没装 Codex：runner 直接返回「跳过」，报告里写 `xmodelState: skipped`，不报错。
- Codex 的发现**走同一条核实流水线**（facts → marker → 判官），不直接采信；它的改写**只采纳问题，不采纳措辞**。
- 原始输出留在 scratch 目录，不写进学生的作业目录。
- 每份作业累计 ≤ 2 次（额度有限）。

