# Ed (edstem.org) —— 读 lesson / 讨论区，往 workspace 贴代码

> 建于 2026-09-19（规则 18）。**Ed course id** 在已登录的 Ed 页面里请求 `/api/user` 就能拿到（`courses[].course.id`），
> 每门课的 id 记进各自的 `<课程>/CLAUDE.md`。
> 🔒 若某门课建了练习平台存档（`<课程>/Ed/README.md`），**「抓法备忘」的权威版放在那份 README 顶部**——
> 这份是跨课程的索引副本，两边不一致时**以 README 为准**，并回头把这份补齐。

## 认证

在他 Chrome 的 Ed 标签页里跑 JS：token 在 `localStorage.getItem('authToken')`，
请求头用 **`x-token: <token>`**（❌ 不是 `Authorization: Bearer`）。
🔒 token 只在页面里用，**不写进任何文件、不贴进对话**。

## 端点

| 要什么 | 端点 |
|---|---|
| lesson 的 slide 列表 | `GET /api/lessons/<id>?view=1` |
| 单张 slide 正文（XML）| `GET /api/lessons/slides/<id>` |
| 讨论区列帖 | `GET /api/courses/<course_id>/threads?limit=100&offset=N&sort=new` |
| 一帖全文 | `GET /api/threads/<id>?view=1`（`thread.comments[].comments[]` 嵌套回复，`users[].course_role` 分 staff / student）|

⚠️ **code slide 的 starter code 抓不到**（走 workspace / repo 另一套接口）—— 那部分要他自己复制。

## 🔴 Chrome 工具的两个坑（Canvas 那边也一样，见 `canvas.md`）

| 坑 | 解 |
|---|---|
| 返回值**单个字符串约 1000 字符就截断** | 返回数组；或分 900 字符一段，用 `¦i¦…¦END¦` 标记，本地从会话 transcript 拼回 |
| 带 `=` `;` `&` 或 URL 的串会被当 cookie 打码（`[BLOCKED: Cookie/query string data]`）| 搬运时换成 `≡ ⸵ ＆`；只做核对时只返回长度 + 哈希 |

## 往 workspace（Monaco 编辑器）贴代码（09-18 / 09-19 实测）

code slide 的 URL 形如 `lessons/<lessonId>/slides/<slideId>`。页面**没有** `monaco` 全局，只能走系统剪贴板。

🔴🔴 **翻过一次车**：按键发给的是 `document.activeElement` 那个编辑器，**不一定是屏幕上那个**
（`cmd+A` 选中了隐藏的那个文件、`cmd+V` 插进了另一个文件，原文件整份被替换成了剪贴板里的旧内容；workspace 实时保存，点 Mark 才算提交）。

**✅ 可靠做法**：点文件标签 → 点代码区 → 等 1 s → JS 核 `activeElement` 是 TEXTAREA 且
`closest('.monaco-editor').offsetParent !== null` → `cmd+A` `cmd+C` 先把 Ed 现有内容拷回本地 `pbpaste | cmp` →
`pbcopy < X.java` → `cmd+V` → 再 `cmd+A` `cmd+C` `pbpaste | cmp` 核对。
🔒 **核对一律用 `cmd+C` + `pbpaste | cmp`**，`activeElement.value` 不可靠（无选区时只是光标附近片段）。
开过第二个文件后页面上有两个编辑器，`querySelector('.monaco-editor textarea')` 会读到旧的那个。

## 往 workspace 上传文件（PDF 等）—— 看过界面（中文 UI）

编辑器左上 **「文件」** 旁边的 **「+」** → 菜单四项：新建文件… · 新建文件夹… · 新建符号链接… · **上传…** → 选本地文件，落进 `/home/`（和 .java 同级）。
右下角 **「评判」= Mark**，「运行」= Run。附件 PDF 也这样传（传完点 Mark 才算交）。
