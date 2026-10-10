export const meta = {
  name: 'prescan-human-layer-and-table-skeletons',
  description: '给一批未讲 deck 补预扫人工层 + 建对照表骨架（默认 build-only，args.verify 才加独立核验）',
  phases: [
    { title: 'Build', detail: '一 deck 一 agent：补 _预扫 人工层 + 建 字幕/<deck>-对照.md 骨架' },
    { title: 'Verify', detail: '独立 agent 跑 pdftotext / git diff / ls 核前置警告、路径、offset、无伪造' },
    { title: 'Fix', detail: '没过的按问题清单修一次，再核一次' },
  ],
}

// 🔒 批量补「未讲 deck」的预扫人工层 + 对照表骨架 —— /总控 专用 Workflow 脚本
// 他 2026-09-03 23:47 定的两条：① 这活用 opus 跑（agent 全部 model:'opus'）
//                              ② 只在他空闲、不用 token 的时候跑，学习/做作业的晚上不许起
// 跑法（在 /总控 会话里）：Workflow({scriptPath: "shared/工具/workflow-预扫骨架.js", args: {root: "<项目根绝对路径>", decks: [...]}})
//   args.root     🔒 必填：项目根绝对路径（脚本里不再写死任何本机路径）
//   args.scratch  当前会话的 scratchpad 路径（渲染页面用；默认 /tmp）
//   args.model    agent 用的模型（默认 'opus'）
//   args.verify   true 才起核验/修补两级（默认 build-only）
//   args.samplePrescan / args.sampleTable  给 agent 当格式样板的两个现成文件（默认指向示例课 algorithms 的 w05 那两份；
//                 你的项目里没有就换成自己最规整的一份预扫文件和一份对照表）
//   decks 每项：{course, deck, line(台账行号), hasHuman(预扫已有人工层?), tableExists(对照表已有?), title}
//   加 refresh:true + fixes:'…' 走修补模式（老格式人工层只改点名的过期项，不重写）
//   示例 decks 清单见 shared/工具/workflow-预扫骨架-args.json（跑前先重新 ls 核一遍缺口）
// 09-03 23:4x 首次起跑后被他叫停（要留 token 学习），没写出任何文件，git 干净。
// 09-04 12:41 他定：不跑 verify（省额度）—— 默认 build-only；args.verify === true 才起核验/修补两级。

const SCRATCH = args.scratch || '/tmp'   // 🔒 跑之前把当前会话的 scratchpad 路径用 args.scratch 传进来（写死会指向早就没了的旧会话，09-24 踩过）
const DECKS = args.decks
const ROOT = args.root                    // 🔒 项目根绝对路径，必须从 args 传入
if (!ROOT || ROOT.indexOf('<') !== -1) throw new Error('args.root 必须传项目根的绝对路径（示例 args.json 里的 "<项目根绝对路径>" 要换成真实路径）')
if (!Array.isArray(DECKS) || DECKS.length === 0) throw new Error('args.decks 不能为空：每项 {course, deck, line, hasHuman, tableExists, title}')
const MODEL = args.model || 'opus'
const SAMPLE_PRESCAN = args.samplePrescan || 'algorithms/课件/_预扫/w05-L1.md'
const SAMPLE_TABLE = args.sampleTable || 'algorithms/字幕/w05-L2-对照.md'

const BUILD_SCHEMA = {
  type: 'object',
  required: ['course','deck','prescanHumanLayerWritten','tableCreated','offset','isContinuation','trueContentPages','estHours','prereqWarnings','srtNote','ledgerNote','issues'],
  properties: {
    course: {type:'string'}, deck: {type:'string'},
    prescanHumanLayerWritten: {type:'boolean'}, tableCreated: {type:'boolean'},
    offset: {type:'string'}, isContinuation: {type:'boolean'},
    trueContentPages: {type:'integer'}, estHours: {type:'string'},
    prereqWarnings: {type:'array', items:{type:'object', required:['page','term','definedIn','status'], properties:{page:{type:'string'}, term:{type:'string'}, definedIn:{type:'string'}, status:{type:'string'}}}},
    srtNote: {type:'string'}, ledgerNote: {type:'string'},
    issues: {type:'array', items:{type:'string'}},
  },
}
const VERIFY_SCHEMA = {
  type: 'object', required: ['deck','pass','problems','checked'],
  properties: { deck:{type:'string'}, pass:{type:'boolean'}, problems:{type:'array', items:{type:'string'}}, checked:{type:'array', items:{type:'string'}} },
}

function paths(d) {
  return {
    pdf: `${d.course}/课件/${d.deck}.pdf`,
    prescan: `${d.course}/课件/_预扫/${d.deck}.md`,
    table: `${d.course}/字幕/${d.deck}-对照.md`,
    claude: `${d.course}/CLAUDE.md`,
  }
}

function buildPrompt(d) {
  const p = paths(d)
  const partA = d.hasHuman
    ? `A. 预扫文件 \`${p.prescan}\` —— **已经有人工层**（旧格式）。跳过 A 不改它；但要把它的前置警告 / 📗 / 分块 读出来用在 B 里。`
    : `A. 预扫文件 \`${p.prescan}\` —— 现在只有脚本段（机械事实 + 逐页骨架）。**在文件末尾追加人工层**。🔒 脚本段一个字不许改、不许删、不许重排；追加前先写一行 \`---\`。`
  const partB = d.tableExists
    ? `B. 对照表 —— \`${d.course}/字幕/\` 里已有覆盖这份 deck 的对照表，**跳过 B，什么都别建**。`
    : `B. 对照表 \`${p.table}\` —— 不存在，要建骨架。🔒 先 \`ls\` 确认不存在再建；存在就绝不覆盖、绝不改。`
  return `你是 /总控 会话派出的 agent，项目根 ${ROOT}（先 cd 进去）。任务：给 ${d.course} \`${d.deck}\`（${d.title}）补两样基建。**只写下面点名的文件，别的文件一个字不许动**（尤其 shared/ 下的账本、台账、别的 deck 的文件），不许 git commit。

## 目标文件
${partA}
${partB}

## 先读 —— 🔒 只读这几个，别的一概不读（09-04 他问「为什么烧那么多 token」后定的：每个 agent 上一批 45–60 次工具调用、10–14 M 输入 token，病根是自己逐页 pdftotext + 逐词 grep 十份 deck）
1. 台账那一行：\`sed -n '${d.line}p' shared/lecture台账.md\` —— 字幕文件名、offset、Reading、Canvas 备注都在里面。🔒 **字幕文件名以它为准**
2. 预扫脚本段：\`${p.prescan}\`（机械事实 + 逐页骨架；已有人工层的整份读）
3. 🔑 **deck 全文**：\`${d.course}/课件/_预扫/${d.deck}.txt\`（pdftotext 已经跑好了，带 \`===== ${d.deck} viewer p.N =====\` 页标）—— **一次 Read 读完，不许再对 PDF 跑 pdftotext**；超过 60 KB 的用 sed 按页标分两三段读
4. 🔑 **首次出现 / 定义在哪** 只查一个文件：\`${d.course}/课件/_预扫/_全课文字层.txt\`（全课 deck 按周序拼的，带页标）—— **把要查的词拼成一次 \`grep -n -i -E 'a|b|c'\`**，看命中行往上最近的页标就是出处。⚠️ 只有文字层，图片里的字读不出，「零命中」写成「文字层零命中」
5. 样板（各读一份就够）：\`sed -n '/## 人工三项/,$p' ${SAMPLE_PRESCAN}\`（人工层长什么样）· \`${SAMPLE_TABLE}\`（骨架长什么样，含「页码口径」「📗」「🔴 点破」「逐页校对表」四节）
6. \`${p.claude}\` 里的教材 / tute / Ed / 考核范围那几节（用 grep -n 定位后 sed 取，别整份 cat）
7. 📗 路径：\`ls ${d.course}/tute ${d.course}/教材 ${d.course}/例卷 2>/dev/null\` **一次列完**（该课若还有别的资料目录，例如论坛存档 \`${d.course}/Ed\`，一并列）—— 之后写 📗 表只从这个清单里挑，不再逐条 ls
8. \`grep -n '${d.deck}' shared/公告.md\` 一次（引用时写「日期 + 标题关键词」，不写行号）

🔒 **硬上限：工具调用总数 ≤ 20 次**（上面 8 条算 8 次左右，写文件 2 次，剩下的给渲染和补查）。**不许**读 \`.claude/commands/预扫.md\`（格式已全写在下面）· **不许**读别的 deck 的对照表（样板那一份除外）· **不许**对任何 PDF 跑 pdftotext · **不许**打开别的 deck 的 .md/.txt（用索引）。渲染（pdftoppm + Read 图）**≤ 3 页**，只渲染骨架里内容为空或只有标题的页；其余「空页/动画帧」在人工层写「⬜ 未渲染核，按脚本段口径」。

## 人工层要写的（追加到预扫文件末尾：先 \`---\`，再 \`## 人工三项\`）
- **标题编号**：课件内部 Lecture 编号 + 标题 + lecturer；标题有没有 (1)(2) 之类分册编号；**p.2 的页脚号是多少**（🔒 判续篇看 p.2 不看封面 —— 有的课每份封面页脚都印 1，会骗人）
- **在整门课的位置**：接哪份、后面是哪份（从 deck 结尾的 "next time" / 台账备注 / 课程 CLAUDE.md 的周表取，写明依据）
- **🔴 前置警告表**：\`| 用到的东西 | 定义在哪 | 状态 |\`。🔒 只列**真前置**：正文用到而本 deck 没定义的符号 / 术语 / 公式。每条必须写「本 deck 第 X 页用到」+「定义在 <course> \`<deck>\` 第 Y 页」，**两个页码都要你用 pdftotext 实际核过**。已讲过的（看台账那门课「讲到」列）标 ✅，没讲过的标 🔴 并写「讲到 p.X 之前要先补 \`<deck>\` p.Y」。查不到定义处的写「本 deck 首次出现，讲到当场点破」。写不出依据的条目宁可不写。
- **📗 其他资料对应段落**：\`| 课件页 | 哪个文件 | 行号/章节 | 那边独有的 |\`。🔒 只列磁盘上真实存在的文件，**每条先 ls 核路径**。来源：tute 讲义（\`${d.course}/tute/\`）· 教材（\`${d.course}/教材/\`，章节号从台账 Reading 备注或课程 CLAUDE.md 取，没有就写「章节待定」不许编）· 该课的论坛 / Ed 存档（若 \`${d.course}/Ed/*.md\` 之类存在，给行号范围；对应材料还没归档就明写「⬜ 未归档 —— 留给 /总控 抓」）· \`shared/公告.md\` 里点名这份 deck 的条目（\`grep -n '${d.deck}' shared/公告.md\`，同时 grep deck 标题关键词）。🔒 **引用 \`shared/公告.md\` 一律写「日期 + 条目标题关键词」，不写行号** —— 别的会话随时往里插条目，行号一天就飘（09-04 已飘过两次）。workshop 和 Lab 这类也算 tute。
- **概念清单**：本 deck 新引入的术语，🔒 一律英文、照课件拼写
- **分块建议**：按内容分块，🔒 **一块 ≤ 5 页真内容**（动画段算 1 页）；每块标「铺垫」或「硬核」；行政 / 封面 / 版权页单独一行「一句话带过」
- **估时**：🔒 唯一口径 —— 概念页 10–12 min · 背景/铺垫页 5–7 min · 行政页 ~1 min · 一讲收尾卷 30–45 min；给总数和分解。别用别的数字。
- 🔴 **脚本报「空页」或「动画重复帧」的页**（看脚本段的动画段清单 + 逐页骨架里内容为空或只有标题的行，抽最可疑的 3–6 页）：\`pdftoppm -png -r 80 -f N -l N ${p.pdf} ${SCRATCH}/${d.course}-${d.deck}-p\` 渲染出来，用 Read 看图，在人工层写「渲染核过：p.N 实际是 ___」。pdftotext 读不出图片页，09-02 这里判错过 —— **不渲染不许对任何页下「是空的 / 只是重复帧」的结论**。
- 如果这份 deck 是**只有动画版**（整讲每个动画帧各占一页），分块按「不同画面」算页，并在人工层写清 viewer 页区间怎么对应画面。

## 对照表骨架要写的（新建 \`${p.table}\`）
- 标题：\`# ${d.course} \\\`${d.deck}\\\` 对照表 —— <deck 标题>\`
- 引言 blockquote：deck 路径 + 页数 · lecture 日期 / lecturer（台账里有）· **数据源 = 台账点名的字幕文件**（照台账写；\`⏳待发\` 的写「字幕 ⏳待发 —— /学 开局先问他这节去没去、能不能下录音；没字幕就只能按课件讲，口头层留空」）· 「**骨架 2026-09-04 由 /总控 建好，逐页校对还没做。** 🔒 开讲前讲哪几页先校对哪几页，没校对的页不许开讲」
- \`## 页码口径\`：offset（从预扫脚本段抄，写成「viewer p.N = 页脚 N+k」；没页脚号的 deck 写「viewer 页是唯一一套页码，不存在 offset」）· 是不是续篇、接哪份 · 动画段清单
- \`## 📗 其他资料对应段落\`：和人工层同一张表
- \`## 🔴 讲之前就知道要点破的\`：前置警告表里 🔴 的那几条（没有就写「无 —— 这一讲自足」）
- \`## 逐页校对表\`：默认只放说明 blockquote + 表头 + 一行 \`| | | | ⬜ 还没校对 —— 讲到哪校到哪 |\`。🔒 **不许凭印象写老师原话** —— 但如果你为了核覆盖度真的读了 \`.srt\` 并挖到课件上没有的 🔴 考点 / ⚪ 不考，**就按表的格式写成真行（页 + 时间戳 + 标记 + 原话），别塞进头部或前置表**（09-04 栽过：原话进了头部还删了时间戳，校对的人得重新定位）。写不出时间戳的一条都不许写。

## 硬规矩
- 只写点名的那一两个文件。不碰 shared/、不碰台账、不 git commit。
- 每个页码、每条路径、每个「定义在哪」都要实测（pdftotext / ls / grep），不许凭印象。
- 中文叙述、术语英文；不用 LaTeX 公式，用反引号。
- 最后用 StructuredOutput 返回。\`ledgerNote\` 是给总控抄进台账备注列的一句话，格式：\`📝 对照表骨架已建 \\\`字幕/${d.deck}-对照.md\\\`（09-04）· 人工层已补 \\\`_预扫/${d.deck}.md\\\`：真内容 N 页 / 估时 ≈X h / 前置：<一句话或「自足」>\`（跳过了哪一半就只写另一半）。\`issues\` 写你没法确定、要总控或 /学 会话回头处理的事。`
}

function verifyPrompt(d, b) {
  const p = paths(d)
  return `你是独立核验 agent，项目根 ${ROOT}（先 cd 进去）。另一个 agent 刚给 ${d.course} \`${d.deck}\` 写了基建文件，它自报：${JSON.stringify(b)}。**你的任务是找错，默认它有错。** 🔒 你只读不写（除了把 pdftotext / pdftoppm 输出放到 ${SCRATCH}/verify-${d.course}-${d.deck}-*）。

要核的文件：预扫 \`${p.prescan}\`${d.tableExists ? '（对照表已有，本项不核）' : `，对照表 \`${p.table}\``}。格式定义在 \`.claude/commands/预扫.md\`。

逐条核，**每条都要跑命令**，不许只读文件就打勾：
1. ${d.hasHuman ? '预扫文件本来就有人工层，核它这次**没被改**：`git diff -- ' + p.prescan + '` 必须为空。' : '⚠️ 这个仓库每回合自动 commit，别拿 HEAD 当基线。基线 = 这份预扫文件**第一次落盘**的 commit：`B=$(git log --format=%h --follow -- ' + p.prescan + ' | tail -1)`；`N=$(git show $B:' + p.prescan + ' | wc -l)`；`diff <(git show $B:' + p.prescan + ') <(head -n $N ' + p.prescan + ')` 必须为空（脚本段逐字节相同，允许仅差尾部空行）；当前行数 − N 应 > 20（人工层真的写进去了）。文件末尾必须有 `## 人工三项`、前置警告表、📗 表、概念清单、分块建议、估时。'}
2. ${d.tableExists ? '（跳过）' : `对照表 \`${p.table}\`：存在；含 \`## 页码口径\`、\`## 📗\`、\`## 逐页校对表\` 三节；逐页校对表里除表头、分隔行和 ⬜ 说明行外**没有任何内容行**；\`grep -nE '[0-9]{2}:[0-9]{2}:[0-9]{2}' ${p.table}\` 必须为空（骨架不许带录音时间戳、不许有老师原话）。`}
3. 对照表和人工层里的 offset、续篇判断、动画段，和预扫脚本段那几行（\`页脚 offset\`、\`第一个带页脚号的页\`、\`动画段\`）一致；再抽 p.2 和末页各跑一次 \`pdftotext -layout -f N -l N ${p.pdf} - | tail -3\` 看页脚号，核 offset 是真的。
4. 人工层「前置警告」**每一条**：用 \`pdftotext -layout -f X -l X ${p.pdf} -\` 核「本 deck 第 X 页用到」那个词真的在那页；用 pdftotext 核「定义在 <deck> 第 Y 页」那页真的有定义。核不到的逐条列出。标 ✅/🔴 的状态要和 \`sed -n '${d.line}p' shared/lecture台账.md\` 所在课程节里那份前置 deck 的「讲到」列一致（grep 那门课的行）。
5. 📗 表里每条路径 \`ls\` 存在；教材章节号如果台账 Reading 备注和课程 CLAUDE.md 里都没有而人工层写了具体节号，标「来源不明」；行号范围抽一条 \`sed -n\` 核内容对得上。
6. 分块建议每块 ≤ 5 页真内容；估时用的是 10–12 / 5–7 / ~1 min + 收尾卷 30–45 min 这套口径。
7. 人工层有没有对某页下「是空的 / 只是重复帧 / 这份 deck 没有 X」的断言而没写「渲染核过」。
8. 概念清单是不是英文。
9. \`git status --porcelain\` 里，除了 \`${p.prescan}\`${d.tableExists ? '' : ' 和 `' + p.table + '`'} 之外，**不许有这份 deck 的 agent 改出来的别的文件**（shared/、台账、别的 deck）。注意：别的 deck 的 agent 在并行跑，它们自己那两个文件出现在 git status 里是正常的；你只盯 shared/ 和 .claude/ 有没有被动。

只有 1–9 全过才 pass=true。\`problems\` 每条写清文件、行号、错在哪、该怎么改（给修的人直接照做）。\`checked\` 列你实际跑过的命令。`
}

function fixPrompt(d, b, v) {
  return buildPrompt(d) + `

## ⚠️ 这是修补轮
上一轮你（或另一个 agent）已经写过这两个文件，独立核验报了下面的问题。**只修这些问题，别的内容不动**（尤其不许重写整份文件、不许再动脚本段）：
${(v && v.problems ? v.problems : ['（核验 agent 没返回问题清单 —— 自己按上面的规矩逐条自查一遍再改）']).map((x, i) => `${i+1}. ${x}`).join('\n')}

改完按同样的 StructuredOutput 返回（ledgerNote 照旧）。`
}

// 🔧 refresh 模式（09-05 加）：老格式（编号七节）的人工层有过期项 —— 不重写，只按 d.fixes 做最小修改
function refreshPrompt(d) {
  const p = paths(d)
  return `你是 /总控 会话派出的 agent，项目根 ${ROOT}（先 cd 进去）。任务：给 ${d.course} \`${d.deck}\`（${d.title}）的预扫文件 \`${p.prescan}\` 做**最小修补** —— 它是 08 月写的老格式（编号七节）人工层，09-04 建对照表时核出下面这些过期/错误项。**只改点名的地方，别的一个字不动**；不许动别的文件（尤其 shared/ 账本、对照表），不许 git commit。

## 要改的项（逐条做，每条改完在那一行末尾加「（09-05 修）」）
${d.fixes}

## 先读 —— 🔒 只读这几个，工具调用总数 ≤ 12 次
1. \`cat -n ${p.prescan}\`（整份，才 100 多行）
2. 对照表 \`${p.table}\`（09-04 建的，里面「🔴 讲之前就知道要点破的」一节和「页码口径」节是**最新的前置结论和字幕事实**，以它为准往预扫里改）
3. 台账那一行：\`sed -n '${d.line}p' shared/lecture台账.md\`
4. 要核某页内容时用 \`${d.course}/课件/_预扫/${d.deck}.txt\`（全文带页标，sed 取那几页），**不许 pdftotext**
估时口径（官方）：概念/要算的页 10–12 min · 铺垫页 5–7 min · 行政页 ~1 min · 收尾卷 30–45 min。

## 怎么改
用 Edit 工具做精确替换（old_string 必须唯一），或 python 做 count==1 的替换。改完 \`git diff --stat -- ${p.prescan}\` 看一眼改动行数是不是只有那几处。

## 返回（StructuredOutput）
按 schema 填：prescanHumanLayerWritten=true · tableCreated=false · offset / isContinuation / trueContentPages / estHours 填修补后文件里的值 · prereqWarnings 填修补后的前置表（只列 🔴 项）· srtNote 一句 · ledgerNote 一句「📝 预扫人工层 09-05 修补：改了哪几项」· issues 里放你没法确认的点。`
}

log(`共 ${DECKS.length} 份 deck，一份一条流水线：build → verify → (fix → reverify)`)

// 🔒 他 2026-09-04 12:41 定：「为啥还要 verify 不用 verify 了吧」—— 默认只跑 build；要核验传 args.verify === true
const VERIFY = args.verify === true
const results = await pipeline(
  DECKS,
  d => agent(d.refresh ? refreshPrompt(d) : buildPrompt(d), { label: `${d.refresh ? 'refresh' : 'build'}:${d.course}/${d.deck}`, phase: 'Build', schema: BUILD_SCHEMA, model: MODEL }),
  (b, d) => {
    if (!b) return null
    if (!VERIFY) return { b, v: null }
    return agent(verifyPrompt(d, b), { label: `verify:${d.course}/${d.deck}`, phase: 'Verify', schema: VERIFY_SCHEMA, model: MODEL })
      .then(v => ({ b, v }))
  },
  async (r, d) => {
    if (!r) return { course: d.course, deck: d.deck, failed: true }
    if (!VERIFY) return { ...r.b, verify: null, fixed: false }
    if (r.v && r.v.pass) return { ...r.b, verify: r.v, fixed: false }
    log(`${d.course}/${d.deck} 没过核验（${r.v ? r.v.problems.length : '?'} 条），修一轮`)
    const b2 = await agent(fixPrompt(d, r.b, r.v), { label: `fix:${d.course}/${d.deck}`, phase: 'Fix', schema: BUILD_SCHEMA, model: MODEL })
    const v2 = await agent(verifyPrompt(d, b2 || r.b), { label: `reverify:${d.course}/${d.deck}`, phase: 'Fix', schema: VERIFY_SCHEMA, model: MODEL })
    return { ...(b2 || r.b), verify: v2, fixed: true, firstVerify: r.v }
  },
)

const okc = results.filter(r => r && !r.failed).length
log(VERIFY ? `完成：${results.filter(r => r && r.verify && r.verify.pass).length}/${DECKS.length} 份核验通过` : `完成：${okc}/${DECKS.length} 份 build 返回（未核验）`)
return results