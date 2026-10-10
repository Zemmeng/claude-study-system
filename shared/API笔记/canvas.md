# Canvas —— 在他的 Chrome 里调 REST API，拿签名直链，curl 下到本地

> 建于 2026-09-18（规则 18：探过的 API 当场记档，下次先读这份，不许重探）。
> 实战：总控会话里多次批量下课件 / tute / 作业 spec（一次下过 20 多个文件）。

## 课程 ID

course id = 课程首页 URL `https://<学校的 Canvas 域名>/courses/<id>` 里那串数字。
**每门课的 id 记进各自的 `<课程>/CLAUDE.md`**，别每次重新翻。

## 🖥️ 浏览器与登录态

- 用的是**他 Chrome 里已登录的会话**（`claude-in-chrome` 扩展）。`list_connected_browsers` 若列出多台机器，
  🔒 **开标签页之前先看落在哪台上** —— 开到没登录的那台是白开一次。
- 🔴 **Canvas 的会话很容易过期**（撞过好几次）。过期的表现是 `/api/v1/...` 回 404 + 页面跳到学校的 SSO 登录页。
  解法：把标签页导到 Canvas 的登录页停住，**让他自己点登录**（🔒 不许代输密码），登完再继续调 API。

## 做法（三步）

**① 开自己的标签页**：`claude-in-chrome` 的 `tabs_context_mcp {createIfEmpty:true}` → `navigate` 到任意一门课首页（`https://<Canvas 域名>/courses/<id>`），借他已登录的会话。
⚠️ **别碰别的会话正在用的标签页**；用完 `tabs_close_mcp` 关掉。

**② `javascript_tool` 里 fetch 同源 API**（要剥掉 `while(1);` 前缀）：

```js
const g = async u => { const r = await fetch(u,{headers:{Accept:'application/json'}});
  const t = await r.text(); try { return JSON.parse(t.replace(/^while\(1\);/,'')); }
  catch(e){ return {ERR:r.status, head:t.slice(0,200)}; } };
```

| 要什么 | 端点 |
|---|---|
| 某门课的周模块 + 每项 | `/api/v1/courses/<id>/modules?include[]=items&per_page=50` |
| Page 正文（有的课，课件链接藏在 Page 里）| `/api/v1/courses/<id>/pages/<page_url>` → 从 `body` 里正则抓 `files/(\d+)` |
| 作业详情（spec 附件也在 `description` 里）| `/api/v1/courses/<id>/assignments/<aid>` |
| 公告 | `/api/v1/announcements?context_codes[]=course_<id>&start_date=YYYY-MM-DD&end_date=…` |
| 单个文件元数据 | `/api/v1/files/<fid>`（`display_name` · `size` · `updated_at` · `locked_for_user`）|
| 🔑 **签名直链** | `/api/v1/files/<fid>/public_url` → `{public_url: "https://inst-fs-…?token=…"}`，**约 24 h 有效** |
| 自己的成绩 | `/api/v1/courses/<id>/students/submissions?student_ids[]=self&per_page=50` |

**③ Bash 里 `curl -sfL -o <本地路径> "<public_url>"`** —— 不经过浏览器下载框，直接落进课程目录。

## 🔴 三个坑

| 坑 | 症状 | 解 |
|---|---|---|
| **学生看不了 Files 列表** | `/courses/<id>/files` 回 `unauthorized` | 改走 **modules → items**（`type:'File'` 的 `content_id` 就是 file id）；Page 里的文件从正文抓 |
| 🔴 **`javascript_tool` 返回的单个字符串超过约 1000 字符会被截断** | 签名直链（约 470 字符）拼成一个大字符串返回，第三条起被截 | **返回数组**（每个元素是短字符串）就不会截；或先存 `window.__x` 再分批取 |
| 解答文件有锁 | `locked_for_user: true`，`public_url` 拿不到 | 记一笔「Canvas 还锁着」，过了解锁日再下 |

## 命名（下到哪）

课件 `<课程>/课件/wNN-LN.pdf` · tute `<课程>/tute/wNN-<内容>.pdf` · 作业 spec `<课程>/作业/wNN-<项目>-spec.pdf`（NN = due 那周）· 代码 `<课程>/代码/wNN-LN.py`。
下完**当场跑预扫**：`python3 shared/工具/预扫.py <pdf> --full --save` → `python3 shared/工具/文字层索引.py <课程>`。
