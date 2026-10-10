# `shared/审计/` —— 大规模 subagent 的落盘区

跑多 agent 的审计 / 审查时，**每个 agent 先把完整发现写进 `shared/审计/<维度key>.md`，再返回结构化结果**。

为什么：token 限额会把整个 workflow 当场切断。没落盘的 agent，成果就跟着丢了；
落了盘的，续跑时开工前先看 `shared/审计/<维度key>.md` 在不在 —— **在就读出来当结果返回，不重跑**。

收尾：主会话把这里的 `*.md` 归并成一份留档（`shared/审计-<主题>-<日期>.md`），然后清空本目录（这份 README 留着）。

做法和 prompt 模板 → `shared/工具手册.md` 末节。
