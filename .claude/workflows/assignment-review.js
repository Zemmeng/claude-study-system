export const meta = {
  name: 'assignment-review',
  description: '作业交前审查（单轮）：按镜头并行找问题 → 合并去重 → 对抗式核实 → 判官分级出报告。只接受学生确认过的 allowed / guided 档',
  whenToUse: '只由 /审查 命令调用（.claude/commands/审查.md）；它负责确认档位、机械预检、拼 args 并先问学生「开始？」',
  phases: [
    { title: 'Find', detail: '每个镜头一个 sonnet finder（+ 可选跨模型 runner）', model: 'sonnet' },
    { title: 'Merge', detail: '一个 sonnet 去重、剔除已知 / 已裁定、核引文', model: 'sonnet' },
    { title: 'Verify', detail: 'facts 核事实 → marker 判会不会扣分', model: 'sonnet' },
    { title: 'Judge', detail: '主模型复现必改项、分级、写报告' },
  ],
}

// ============================================================ 参数与闸门
// 设计：docs/作业审查.md。档位：
//   allowed  —— 作业允许 AI：可以给改法（OLD → NEW）
//   guided   —— 不允许 AI 产出最终件：只指出位置、违反的规则、为什么扣分，不给改好的答案 / 句子 / 代码
//   forbidden —— spec 禁止任何 AI 使用：本 workflow 拒跑（用 作业审查.py checklist 出自查清单）
const A = args || {}
for (const k of ['root', 'course', 'code', 'round', 'tier', 'tierConfirmed', 'lenses', 'scratch', 'manifestDigest']) {
  if (A[k] === undefined || A[k] === null || A[k] === '') throw new Error(`args.${k} 必填（这个 workflow 只由 /审查 调用）`)
}
if (A.tier === 'forbidden') throw new Error('forbidden 档不跑 AI 审查 —— 用 python3 shared/工具/作业审查.py checklist')
if (!['allowed', 'guided'].includes(A.tier)) throw new Error(`未知档位 ${A.tier}`)
if (A.tierConfirmed !== true) throw new Error('档位没有经过学生确认（tierConfirmed 必须是 true）')
if (A.xmodel !== undefined && typeof A.xmodel !== 'boolean') throw new Error('xmodel 只接受 true / false（自定义跨模型 CLI 还没实现）')

const SONNET = 'sonnet'                         // 🔒 每个扇出 agent 都显式写 model，只有判官继承主模型
const JUDGE_OPTS = A.judgeModel ? { model: A.judgeModel } : {}
const D = `${A.root}/${A.course}/作业/_审查/${A.code}`
const R = `${D}/r${A.round}`
const MAX = A.largeApproved === true ? (A.maxAgents || 10) : Math.min(A.maxAgents || 10, 10)   // 没有大规模同意，硬上限 10
if (A.verifyBatch !== undefined && !(Number.isInteger(A.verifyBatch) && A.verifyBatch >= 1)) throw new Error('verifyBatch 必须是 ≥ 1 的整数')
const BATCH = A.verifyBatch || 10
const GUIDED = A.tier === 'guided'
const HINT = GUIDED && A.hint === 'question'
const XMODEL = A.xmodel === true
const lensId = l => l.key + (l.shard ? `__${l.shard}` : '')
const lenses = A.lenses.filter(l => (l.tiers || []).includes(A.tier))
const skippedLenses = A.lenses.filter(l => !(l.tiers || []).includes(A.tier)).map(lensId)

if (lenses.length === 0) throw new Error('这一档下没有可用的镜头')
const ids = lenses.map(lensId)
if (new Set(ids).size !== ids.length) throw new Error(`镜头重复：${ids.join('、')}（同一个镜头要跑多份请给 shard）`)
// 起步预算：finder + xmodel + merge + 1 facts + 1 marker + judge
const planned = lenses.length + (XMODEL ? 1 : 0) + 4
if (planned > MAX && A.largeApproved !== true) {
  throw new Error(`计划 ${planned} 个 agent，超过上限 ${MAX} —— 减少镜头，或者让学生单独同意大规模运行（largeApproved: true）`)
}
if (planned > MAX) throw new Error(`计划 ${planned} 个 agent，超过 maxAgents ${MAX}`)
if (skippedLenses.length) log(`档位 ${A.tier} 下跳过的镜头：${skippedLenses.join('、')}`)
// guided 的「新数字」检查要用作答和规则里出现过的数（作业审查.py lenses 输出的 knownNumbers）
const KNOWN = Array.isArray(A.knownNumbers) ? new Set(A.knownNumbers.map(String)) : null
if (GUIDED && !KNOWN) log('⚠️ 没有传 knownNumbers：guided 档的「新数字」检查关闭，只靠措辞规则和 scrub / scan 收口')

if (A.dryRun === true) {
  return { dryRun: true, tier: A.tier, round: A.round, dir: D, finders: ids, xmodel: XMODEL,
           skippedLenses, plannedAgents: planned, maxAgents: MAX, retryHeadroom: MAX - planned }
}

// ============================================================ 小工具
// 纯 JS 的字符串哈希（不能用 Date / random —— 会破坏续跑）：缓存键 = 输入指纹
const h = s => { let x = 5381; for (const c of String(s)) x = ((x << 5) + x + c.codePointAt(0)) | 0; return (x >>> 0).toString(36) }
const chunk = (xs, n) => { const out = []; for (let i = 0; i < xs.length; i += n) out.push(xs.slice(i, i + n)); return out }
let used = 0
const left = () => MAX - used
// 每个缓存键都带上：档位 · hint · 输入指纹（材料 + README 手写节 + settled + 自定义镜头）· 轮次 · 类型
const BASEKEY = [A.tier, HINT ? 'question' : 'none', A.manifestDigest, A.round, A.type || ''].join('|')
const keyOf = (...parts) => h([BASEKEY].concat(parts).join('|'))
// 返回的 input_key 必须等于这次的键：不等 = agent 读了旧文件，当作没结果。中止信号（aborted）不看键，先放行给下面判
const checked = (r, key, what) => {
  if (r && typeof r.aborted === 'string' && r.aborted.trim()) return r
  if (r && r.input_key !== key) { log(`⚠️ ${what} 返回的 input_key 不是这次的（可能读了旧文件），当作没有结果`); return null }
  return r
}
const isHashMismatch = r => !!r && typeof r.aborted === 'string' && /hash.?mismatch/i.test(r.aborted)
const isSkipped = r => !!r && typeof r.aborted === 'string' && /^\s*skipped/i.test(r.aborted)
const isAborted = r => !!r && typeof r.aborted === 'string' && r.aborted.trim() !== '' && !isSkipped(r)

// ============================================================ schema（按档位裁字段 = 第一层防线）
const S = { type: 'string' }
const ARR_S = { type: 'array', items: S }
const LOC_KEYS = ['file', 'line_start', 'line_end', 'page', 'question']
const RS_KEYS = ['doc', 'locator', 'authority']
const LOCATION = { type: 'object', properties: {
  file: S, line_start: { type: 'integer' }, line_end: { type: 'integer' }, page: { type: 'integer' }, question: S,
}, required: ['file'] }
const RULE_SOURCE = { type: 'object', properties: {
  doc: S, locator: S, authority: { type: 'string', enum: ['spec', 'official-faq', 'rubric', 'slides', 'reference-only'] },
}, required: ['doc', 'locator', 'authority'] }
const BASE_KEYS = ['title', 'severity', 'marks_at_risk', 'location', 'work_quote', 'rule_quote', 'rule_source',
                   'what_is_wrong', 'why_it_costs', 'evidence', 'needs_execution', 'confidence']
const FIX_KEYS = ['suggested_fix', 'old_text', 'new_text', 'adds_lines']
const META_KEYS = ['id', 'lens', 'source', 'raised_by', 'merged_from']
const VERDICT_KEYS = ['finding_id', 'keep', 'confidence', 'reason', 'evidence_checked', 'severity_adjusted', 'marks_at_risk_adjusted', 'p_deduct']

function findingProps() {
  const p = {
    title: S,
    severity: { type: 'string', enum: ['zero', 'deduct', 'risk', 'polish'] },
    marks_at_risk: { type: 'number' },
    location: LOCATION,
    work_quote: { type: 'string', description: '作答里的逐字原文（≤ 200 字符）' },
    rule_quote: { type: 'string', description: '权威文件里的逐字原句（≤ 300 字符）' },
    rule_source: RULE_SOURCE,
    what_is_wrong: S,
    why_it_costs: S,
    evidence: ARR_S,
    needs_execution: { type: 'boolean' },
    confidence: { type: 'number' },
  }
  if (!GUIDED) {
    p.suggested_fix = S
    p.old_text = { type: 'string', description: '源文件中逐字且只出现一次' }
    p.new_text = S
    p.adds_lines = { type: 'boolean' }
  }
  if (HINT) p.guiding_question = { type: 'string', description: '只许是问题，不许含答案' }
  return p
}
const FINDING_REQ = ['title', 'severity', 'marks_at_risk', 'location', 'work_quote', 'rule_quote', 'rule_source',
                     'what_is_wrong', 'why_it_costs', 'evidence', 'confidence']
const FINDING = { type: 'object', properties: findingProps(), required: FINDING_REQ, additionalProperties: false }

const FIND_SCHEMA = { type: 'object', properties: {
  input_key: S, lens: S, aborted: S, findings: { type: 'array', items: FINDING },
  checked_ok: ARR_S, not_checked: ARR_S,
}, required: ['input_key', 'lens', 'findings', 'checked_ok', 'not_checked'] }

const MERGED_ITEM = { type: 'object', properties: Object.assign({}, findingProps(), {
  id: S, lens: S, raised_by: { type: 'integer' }, merged_from: ARR_S,
}), required: ['id', 'lens', 'raised_by'].concat(FINDING_REQ), additionalProperties: false }
const MERGED_SCHEMA = { type: 'object', properties: {
  input_key: S,
  items: { type: 'array', items: MERGED_ITEM },
  dropped: { type: 'array', items: { type: 'object', properties: { source: S, reason: S, note: S }, required: ['source', 'reason'] } },
  coverage: ARR_S, missing_finders: ARR_S,
}, required: ['input_key', 'items', 'dropped', 'coverage'] }

function verdictProps(withFix) {
  const p = {
    finding_id: S, keep: { type: 'boolean' }, confidence: { type: 'number' }, reason: S, evidence_checked: ARR_S,
    severity_adjusted: { type: 'string', enum: ['zero', 'deduct', 'risk', 'polish', 'none'] },
    marks_at_risk_adjusted: { type: 'number' }, p_deduct: { type: 'number' },
  }
  if (withFix) { p.fix_is_safe = { type: 'boolean' }; p.better_fix = S }
  return p
}
const VERDICTS = withFix => ({ type: 'object', properties: {
  input_key: S,
  verdicts: { type: 'array', items: { type: 'object', properties: verdictProps(withFix),
    required: ['finding_id', 'keep', 'reason', 'evidence_checked'], additionalProperties: false } },
}, required: ['input_key', 'verdicts'] })

const JUDGE_SCHEMA = { type: 'object', properties: {
  input_key: S,
  items: { type: 'array', items: { type: 'object', properties: {
    id: S, class: { type: 'string', enum: ['must_fix', 'should_fix', 'leave', 'rejected', 'unverified', 'incomplete'] },
    expected_loss: { type: 'number' }, fix_cost: { type: 'string', enum: ['low', 'medium', 'high'] },
    fix_cost_reason: S, judge_reproduced: { type: 'boolean' }, note: S,
  }, required: ['id', 'class', 'note'] } },
  open_must_fix: { type: 'integer' },
  mark_range: { type: 'object', properties: { low: { type: 'number' }, high: { type: 'number' }, caveat: S } },
  not_covered: ARR_S, operations_checklist: ARR_S, report_path: S,
}, required: ['input_key', 'items', 'open_must_fix', 'not_covered', 'operations_checklist', 'report_path'] }

// ============================================================ guided 档的机械过滤（第三层防线）
// 🔒 LEAK / NUM / STRUCT 和 shared/工具/作业审查.py 保持一字不差（tests 会比对）
const LEAK = /```|~~~|应该?(是|为|改)|应该(用|写)|应写|应当|必须(写成|是)|(应(?!用)|请|建议|需要?|可以?)\S{0,4}(改写|改用|换成|写成|替换)(?!了)|把[^。；;\n]{1,30}?(换成|写成|替换为|替换成|改写为|改写成)(?!了)|改(成|为)(?!了)|(正确|答案)(答案|的?(值|写法|表达式|结果|公式))?(是|为)|答案\s*[:：]|结果(应|是|为)\s*-?\d|should\s+(be|read|say|use|have|equal)|should\s+([A-Za-z]+\s+){1,3}be(?![A-Za-z])|ought\s+to|instead\s+of|in\s+place\s+of|replace\s[^\n]{1,80}?\swith|change\s[^\n]{1,80}?\sto(?![a-z])|did\s+you\s+mean|(correct|right)\s+(answer|value|formula|result)\s+is|the\s+answer\s+is(?!\s+(incomplete|missing|wrong|incorrect|unclear|not))|(?<![A-Za-z])(answer|result)\s*[:：=]|(->|→)\s*(-?\d|["「`])|`[^`\n]{0,80}=[^`\n]{0,80}`/i
const NUM = /\d[\d,]{0,24}\.\d+|\d{1,3}(?:,\d{3})+|\d{3,}/
const STRUCT = /sha-?256(\s*[0-9a-f]{6,64})?|(?<![0-9a-z])(?=[0-9a-f]{0,63}[a-f])[0-9a-f]{12,64}(?![0-9a-z])|(期望损失|暂定分区间|暂定分)\s*≈?\s*[\d.]{1,12}(\s*[–-]\s*[\d.]{1,12})?|第\s*\d{1,6}\s*(行|页|题)|(?<![a-z])(pp?|lines?|pages?)\.?\s*\d{1,6}(\s*[–-]\s*\d{1,6})?|\d{1,9}(\s*[–-]\s*\d{1,9})?\s*(words?(?![a-z])|marks?(?![a-z])|chars?(?![a-z])|个?字符|词|字(?!节)|页|分(?!之|钟))|\.[a-z0-9]{1,5}:\d{1,7}(-\d{1,7})?(?![0-9])|(?<![0-9a-z])F\d{2,5}(?![0-9])|(?<![a-z])(http(\s+error)?|status(\s+code)?)\s*[:：]?\s*\d{3}(?![0-9])|(?<![0-9])\d{1,7},\d{1,7}p(?![a-z])/i
const MASK = '（已隐去：疑似包含答案）'
// 没有豁免：复述规则原句的说法也会被标出来（merge 会把它改写成只剩位置和规则）。可以逐字核实的引用由 作业审查.py scrub / scan 收口时豁免
const esc = x => String(x).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const PATHS = new RegExp('(' + [A.scratch, A.root].filter(Boolean).map(esc).join('|') + ')\\S*', 'g')   // scratch / 项目路径里的数字（会话 id）不是答案
function leaky(text) {
  const t = String(text == null ? '' : text).normalize('NFKC')
  if (LEAK.test(t)) return true
  if (!KNOWN) return false
  const nums = t.replace(PATHS, ' ').replace(new RegExp(STRUCT.source, 'gi'), ' ').match(new RegExp(NUM.source, 'g')) || []
  return nums.some(n => !KNOWN.has(n) && !KNOWN.has(n.replace(/,/g, '')))
}
const pick = (o, keys) => { const out = {}; for (const k of keys) if (o && o[k] !== undefined) out[k] = o[k]; return out }
function scrub(f) {
  if (!f || typeof f !== 'object') return null
  const keep = BASE_KEYS.concat(META_KEYS).concat(GUIDED ? [] : FIX_KEYS).concat(HINT ? ['guiding_question'] : [])
  const out = pick(f, keep)                                                              // 白名单：其他字段一律不要
  if (out.location && typeof out.location === 'object') out.location = pick(out.location, LOC_KEYS)
  if (out.rule_source && typeof out.rule_source === 'object') out.rule_source = pick(out.rule_source, RS_KEYS)
  if (!GUIDED) return out
  const loc = out.location || {}, rs = out.rule_source || {}
  const fields = [out.title, out.what_is_wrong, out.why_it_costs, out.guiding_question, loc.question, loc.file, rs.doc, rs.locator]
    .concat(Array.isArray(out.evidence) ? out.evidence : [out.evidence])
  if (fields.some(x => x && leaky(x))) out.leak_suspect = true
  return out
}
function maskVerdict(v) {
  if (!v || typeof v !== 'object' || typeof v.finding_id !== 'string') return null
  const out = pick(v, VERDICT_KEYS.concat(GUIDED ? [] : ['fix_is_safe', 'better_fix']))
  out.keep = out.keep === true
  out.evidence_checked = Array.isArray(out.evidence_checked) ? out.evidence_checked.map(String) : []
  if (GUIDED) {
    if (leaky(out.reason)) out.reason = MASK
    out.evidence_checked = out.evidence_checked.map(e => leaky(e) ? MASK : e)
  }
  return out
}
// guided：coverage / not_checked 是模型写的自由文本，带答案的整条丢掉（计数写进日志）
let droppedCoverage = 0
const cleanList = (xs, what) => {
  const all = (xs || []).filter(x => typeof x === 'string')
  if (!GUIDED) return all
  const ok = all.filter(x => !leaky(x))
  if (ok.length < all.length) log(`guided：${what} 里 ${all.length - ok.length} 条疑似带答案，丢弃（宁可少写覆盖，也不漏答案）`)
  droppedCoverage += all.length - ok.length
  return ok
}

// ============================================================ prompt 片段
const TIER_BLOCK = GUIDED
  ? '【档位 guided】这份作业不允许 AI 产出最终答案：只指出位置、违反的规则、为什么会扣分。' +
    '绝对不许写出正确的值、表达式、句子、代码或推导，不许用「应该是 / 改成 / should be / replace with」这类说法，不许给代码块；' +
    'evidence 只写位置、命令和 scratch 里的文件路径，不写算出来的正确值；checked_ok / not_checked 只写题号和检查项名称，不写值。' +
    (HINT ? '可以在 guiding_question 里给一个引导性的问题：只许是问题，不许含答案。' : '')
  : '【档位 allowed】这份作业允许 AI：可以给改法。old_text 必须在源文件里逐字出现且只出现一次；new_text 同长或更短，必须加长时 adds_lines=true 并在 suggested_fix 里说明砍哪里。'
const DURABLE = (f, key, extra, redo) =>
  `\n【落盘 —— 返回前必须做】` +
  (redo
    ? `① 这是重做：${redo}不要读 ${f} 的旧内容，全部重做。`
    : `① 开工前先看 ${f}：如果存在、是合法 JSON、并且其中的 input_key 等于 "${key}" → 直接读出来原样返回，不重做；input_key 不一样或者不存在 → 说明输入变了，重做。`) +
  `② 做完先用 Write 把将要返回的、完全相同的 JSON（input_key 填 "${key}"）写到 ${f}（覆盖），再返回。` +
  `写文件只许写它${extra ? '，以及 ' + extra : ''}；不许改任何被审文件。`
const README = `${D}/材料/README.txt`
const COMMON = l => {
  const lim = l.limits || {}
  return `你是严格但公平的阅卷人，在学生提交前预审「${A.course} ${A.code}」（作业类型 ${A.type || '未标注'}），第 ${A.round} 轮。` +
    (A.round > 1 ? '你的价值只在「前几轮漏掉的」和「前几轮改动自己引入的错」。' : '') +
    `\n【先读】${README} —— 里面有：权威文件和角色（spec / faq / rubric / slides / starter / reference / raw-data）、「已知且刻意」（不许报）、` +
    `「已修」（不许重报，除非修复本身错了）、「已裁定 settled」（没有新证据不许重开）。被审的是 ${D}/材料/ 里的冻结副本，不是别处的草稿。` +
    `README 没列出你这个镜头需要的材料 → 写进 not_checked，不凭常识下结论。` +
    `\n【开工校验】对被审文件算 sha256（shasum -a 256 或 sha256sum），和 ${D}/材料/MANIFEST.json 对比；不一致 → 立即返回 {"input_key":"${finderKey(l)}","lens":"${lensId(l)}","aborted":"hash-mismatch","findings":[],"checked_ok":[],"not_checked":[]}（不写盘）。` +
    `\n【证据纪律】每条 finding 必须带：作答原文的逐字引文 + 精确位置 + 权威规则原句和出处。不许发明要求，规则只认 README 里列为权威的文件。` +
    `返回前对每条自问：阅卷人真的会因此扣分吗？官方答疑有没有相反的说法？这是不是一个可辩护的设计选择？答不上来就别报。` +
    `\n最多 ${l.max_findings || 6} 条，按风险排序；零条是合法答案。必须写 checked_ok（查过且没问题的）和 not_checked（没查到的）。` +
    `\n【工具纪律】对被审文件只读；临时文件只放 ${A.scratch}/${lensId(l)}/；不连任何远程服务器或课程平台；长 PDF 用 材料/ 里的文字层，不整份重新提取；` +
    `渲染页面最多 ${lim.render_pages || 3} 页（材料/页面/ 里已有的图片不算）；工具调用 ≤ ${lim.tool_calls || 25} 次。` +
    `\n${TIER_BLOCK}`
}

// ============================================================ Find
phase('Find')
const finderKey = l => keyOf('find', lensId(l), l.prompt)
const runFinder = l => agent(
  COMMON(l) + `\n【本镜头：${l.key} · ${l.title}${l.shard ? ' · 分片 ' + l.shard : ''}】${l.prompt}` +
    DURABLE(`${R}/find-${lensId(l)}.json`, finderKey(l), `${A.scratch}/${lensId(l)}/ 下的临时文件`),
  { label: `find:${lensId(l)}`, phase: 'Find', schema: FIND_SCHEMA, model: SONNET }).then(r => checked(r, finderKey(l), `find:${lensId(l)}`))

const xmodelKey = keyOf('xmodel', A.specFeedAllowed === false ? 'no-spec' : 'spec')
const xmodelPrompt = () =>
  `你是跨模型互审的 runner。照 ${A.root}/shared/API笔记/codex.md 的配方调用 Codex CLI，让它独立审一遍，再把它的发现转成统一格式。` +
  `\n① command -v codex；没有 → 直接返回 {"input_key":"${xmodelKey}","lens":"xmodel","aborted":"skipped: codex 未安装","findings":[],"checked_ok":[],"not_checked":["跨模型互审：codex 未安装，跳过"]}。` +
  `\n② 有：在 ${A.scratch}/xmodel/ 建隔离副本（只拷 ${D}/材料/ 里的文件${A.specFeedAllowed === false ? '，但不拷 spec 全文，只拷要点文件' : ''}），git init，写 prompt.txt：` +
  `要求它按 README.txt 审查、只报会扣分的问题、每条给逐字引文和规则出处。${GUIDED ? 'prompt 里写明：只指出位置和原因，不许给出正确答案、改写或代码。' : ''}` +
  `\n③ 运行（必须 </dev/null，限时 20 分钟；有 timeout 用 timeout 1200，没有就用 perl -e 'alarm 1200; exec @ARGV'）：codex exec --skip-git-repo-check --sandbox workspace-write -C <副本> "$(cat prompt.txt)" </dev/null > ${A.scratch}/xmodel/run.log 2>&1` +
  `\n④ 它的原始输出留在 ${A.scratch}/xmodel/（不写进学生目录），把发现转成 findings（lens 填 "xmodel"）。只采纳问题，不采纳它的改写措辞。` +
  `\n${TIER_BLOCK}` + DURABLE(`${R}/find-xmodel.json`, xmodelKey, `${A.scratch}/xmodel/ 下的文件`)

const jobKeys = ids.concat(XMODEL ? ['xmodel'] : [])
const jobs = lenses.map(l => () => runFinder(l))
if (XMODEL) jobs.push(() => agent(xmodelPrompt(), { label: 'xmodel-runner', phase: 'Find', schema: FIND_SCHEMA, model: SONNET })
  .then(r => checked(r, xmodelKey, 'xmodel-runner')))
const found = await parallel(jobs)
used += jobs.length

// 材料被改过 = 整轮作废（重跑之后也要再查）；没结果 = 重跑一次（预算内），仍缺就记下来，不静默按少的跑
// 其他中止（工具额度用完、报错……）= 没跑成，不能当成「零条发现」
const fatal = rs => {
  if (rs.some(isHashMismatch)) {
    throw new Error('材料在 prep 之后被改过（finder 报 hash-mismatch）—— 重新跑 作业审查.py prep 再审')
  }
  rs.forEach((r, i) => { if (isAborted(r)) { log(`⚠️ ${jobKeys[i] || ''} 中止：${String(r.aborted).slice(0, 80)}（算没跑成）`); rs[i] = null } })
}
fatal(found)
// xmodel 不重跑：每份作业 Codex 最多 2 次，失败就记 failed
let retryIdx = found.map((r, i) => r || jobKeys[i] === 'xmodel' ? -1 : i).filter(i => i >= 0)
const headroom = Math.max(0, left() - 4)                 // 留给 merge + facts + marker + judge
if (retryIdx.length > headroom) {
  log(`⚠️ 预算只够重跑 ${headroom} 个，放弃：${retryIdx.slice(headroom).map(i => jobKeys[i]).join('、')}`)
  retryIdx = retryIdx.slice(0, headroom)
}
if (retryIdx.length) {
  log(`${retryIdx.length} 个 finder 没有结果，重跑一次：${retryIdx.map(i => jobKeys[i]).join('、')}`)
  const again = await parallel(retryIdx.map(i => jobs[i]))
  used += retryIdx.length
  fatal(again)
  retryIdx.forEach((i, j) => { if (again[j]) found[i] = again[j] })
}
const missingFinders = jobKeys.filter((k, i) => !found[i])
const xmodelState = !XMODEL ? 'off' : !found[found.length - 1] ? 'failed' : isSkipped(found[found.length - 1]) ? 'skipped' : 'ran'
if (missingFinders.length) log(`⚠️ 仍然缺：${missingFinders.join('、')} —— 报告里会写明这几个镜头没有跑成`)
if (lenses.every((l, i) => !found[i])) throw new Error('所有镜头都没跑成 —— 不出报告（一份空报告会被误读成「没问题」）')

const raw = []
found.forEach((r, i) => {
  if (!r || isSkipped(r)) return
  ;(r.findings || []).forEach((f, idx) => {
    const c = scrub(f)
    if (c) raw.push(Object.assign(c, { lens: jobKeys[i], source: `${jobKeys[i]}#${idx}` }))
  })
})
const coverageRaw = cleanList(found.filter(r => r && !isSkipped(r)).flatMap(r => (r.checked_ok || []).map(c => `${r.lens}: ${c}`)), 'checked_ok')
const notChecked = cleanList(found.filter(Boolean).flatMap(r => (r.not_checked || []).map(c => `${r.lens}: ${c}`)), 'not_checked')
log(`Find：${raw.length} 条原始发现${GUIDED ? `（${raw.filter(f => f.leak_suspect).length} 条疑似泄漏答案，交给 merge 处理）` : ''}`)

// ============================================================ Merge
let merged = { items: [], dropped: [], coverage: coverageRaw, missing_finders: missingFinders }
const droppedLeakIds = []
let renumbered = false
if (raw.length) {
  phase('Merge')
  const covText = JSON.stringify(coverageRaw)
  if (covText.length > 12000) log(`⚠️ coverage 太长，交给 merge 时截到 12000 字符`)
  const mergeKey = keyOf('merge', JSON.stringify(raw), covText)
  merged = await agent(
    `你负责合并第 ${A.round} 轮所有 finder 的发现（只合并和清理，不判断对错）。先读 ${README} 和 ${D}/settled.json（不存在就当空）。` +
    `\n期望的 finder：${jobKeys.join('、')}；没跑成的：${missingFinders.join('、') || '无'}（原样写进 missing_finders）。` +
    `\n原始发现（JSON，source 是来源编号）：\n${JSON.stringify(raw)}` +
    `\n\n步骤：` +
    `\n1. 剔除并写进 dropped（note 只写原因，不要回显引文内容）：命中「已知且刻意 / 已修 / settled」的（reason: known / already-fixed / settled）；纯口味的（taste）；` +
    `work_quote 在 ${D}/材料/ 的被审文件里 grep 不到逐字原文的（quote-not-found，空白差异可忽略）；` +
    `rule_quote 在 README 列出的权威文件里 grep 不到逐字原句的（rule-quote-not-found）；任何超过 3 个词的引号内文字不是作答或权威文件逐字子串的（quote-not-verbatim）；超出作业范围的（out-of-scope）` +
    (GUIDED ? '' : '；old_text 在源文件里不是恰好出现一次的（old-text-not-unique）；页数已满还要加长、又说不出砍哪里的（cannot-afford-length）') + `。` +
    (GUIDED ? `\n2. 带 leak_suspect 的条目：改写成只剩位置 + 违反的规则 + 为什么扣分，删掉任何正确答案、正确值、改写句子、代码和带答案的 evidence；改写不了就丢弃（leak-suspect-unfixable）。输出里不要再带 leak_suspect 字段。` : '\n2. （本档允许给改法，跳过）') +
    `\n3. 合并：同一文件 + 位置重叠 + 同一个底层问题 = 一条；raised_by = 独立报出它的 finder 数；保留最强的 severity、最具体的位置和规则原句，证据取并集；merged_from 记来源编号。` +
    `\n4. 按 marks_at_risk 从大到小编号 F01、F02 …（不要用标题做编号，不许重复）。每条的 lens 填最先报出它的镜头。` +
    `\n5. coverage：把各镜头的 checked_ok 压成按题 / 按节的覆盖清单（原始 checked_ok 见下），不要当 finding。\n${covText.slice(0, 12000)}` +
    `\n${TIER_BLOCK}` + DURABLE(`${R}/merged.json`, mergeKey),
    { label: 'merge', phase: 'Merge', schema: MERGED_SCHEMA, model: SONNET }).then(r => checked(r, mergeKey, 'merge'))
  used += 1
  if (!merged) throw new Error(`merge 失败。盘上的 find-*.json 都还在，用同样的 args 重跑本 workflow 会从这里续`)
  // dropped 只留来源和原因代码（note 是自由文本，可能带答案）
  merged.dropped = (merged.dropped || []).filter(d => d && typeof d === 'object')
    .map(d => ({ source: String(d.source || ''), reason: /^[a-z][a-z-]{0,40}$/.test(String(d.reason || '')) ? d.reason : 'other' }))
  merged.coverage = cleanList(merged.coverage && merged.coverage.length ? merged.coverage : coverageRaw, 'coverage')
  // id 校验：必须是 F\d+ 且不重复，否则按顺序重编（模型给的 id 不可信）。
  // 盘上的 merged.json 还是旧编号：/审查 收口时 作业审查.py scrub / assemble 会按同样的规则重编（verify / judge 用的是新编号）
  let items = (merged.items || []).map(scrub).filter(Boolean)
  const seen = new Set()
  const okId = i => typeof i.id === 'string' && /^F\d{2,}$/.test(i.id) && !seen.has(i.id) && seen.add(i.id)
  if (!items.every(okId) || items.length !== (merged.items || []).length) {
    log('⚠️ merge 给的编号不合格（重复或格式不对），按顺序重新编号')
    items = items.map((it, n) => Object.assign(it, { id: `F${String(n + 1).padStart(2, '0')}` }))
    renumbered = true
  }
  if (GUIDED) {
    items.filter(i => i.leak_suspect).forEach(i => {
      droppedLeakIds.push(i.id)
      merged.dropped.push({ source: i.id, reason: 'leak-suspect-unfixable', note: '合并后仍含答案性内容' })
    })
    if (droppedLeakIds.length) log(`merge 之后仍有 ${droppedLeakIds.length} 条疑似泄漏答案，丢弃：${droppedLeakIds.join('、')}`)
    items = items.filter(i => !i.leak_suspect)
  }
  merged.items = items
}

// ============================================================ Verify（自适应：facts → 只把 keep 的给 marker）
const forVerifier = i => {
  const c = Object.assign({}, i); delete c.confidence; delete c.raised_by; delete c.merged_from; delete c.source
  if (GUIDED) delete c.evidence
  return c
}
const verdictIndex = Object.create(null)               // 不用 {} —— 模型给的 id 可能是 "constructor"
const incomplete = new Set()
const unverified = merged.items.filter(i => i.severity === 'polish').map(i => i.id)
const toVerify = merged.items.filter(i => i.severity !== 'polish')

async function runBatches(kind, batches, withFix, promptOf, reserve) {
  // 每批一个 agent；没结果或回票不全的批次在预算内重跑一次；只收本批里真有的 id；仍缺票记 incomplete（不算驳回）
  const files = batches.map((b, k) => `${R}/verify-${kind}-b${k + 1}.json`)
  const keys = batches.map(b => keyOf(kind, JSON.stringify(b)))
  const extra = `${A.scratch}/${kind}/ 下的临时文件`
  const run = (k, redo) => agent(promptOf(batches[k]) + DURABLE(files[k], keys[k], extra, redo),
    { label: `${kind}:b${k + 1}`, phase: 'Verify', schema: VERDICTS(withFix), model: SONNET }).then(r => checked(r, keys[k], `${kind}:b${k + 1}`))
  const missingIn = (r, k) => batches[k].filter(i => !((r && r.verdicts) || []).some(v => v && v.finding_id === i.id)).map(i => i.id)
  const res = await parallel(batches.map((b, k) => () => run(k)))
  used += batches.length
  // 重跑只用 reserve 之外的余量：facts 要给后面的 marker 批次和 judge 留位，marker 要给 judge 留位
  const redo = res.map((r, k) => missingIn(r, k).length ? k : -1).filter(k => k >= 0).slice(0, Math.max(0, left() - reserve))
  if (redo.length) {
    log(`${kind}：${redo.length} 个批次没有结果或回票不全，重跑一次`)
    const again = await parallel(redo.map(k => () => run(k, res[k] ? `上一次缺了这几条的票：${missingIn(res[k], k).join('、')}。` : '')))
    used += redo.length
    redo.forEach((k, j) => {
      if (again[j]) res[k] = { verdicts: ((res[k] && res[k].verdicts) || []).concat(again[j].verdicts || []) }
    })
  }
  res.forEach((r, k) => {
    const want = new Set(batches[k].map(i => i.id))
    ;((r && r.verdicts) || []).forEach(v => {
      const m = maskVerdict(v)
      if (!m || !want.has(m.finding_id)) return
      verdictIndex[m.finding_id] = verdictIndex[m.finding_id] || Object.create(null)
      verdictIndex[m.finding_id][kind] = m
    })
    batches[k].forEach(i => { if (!(verdictIndex[i.id] && verdictIndex[i.id][kind])) incomplete.add(i.id) })
  })
}

if (toVerify.length) {
  phase('Verify')
  const factsRoom = Math.max(0, left() - 2)              // 留 marker 和 judge 各一个
  let batches = chunk(toVerify, BATCH)
  if (batches.length > factsRoom) {
    const over = batches.slice(factsRoom).flat()
    log(`⚠️ 预算只够 ${factsRoom} 个 facts 批次，${over.length} 条没进核实，记为 unverified：${over.map(i => i.id).join('、')}`)
    over.forEach(i => unverified.push(i.id))
    batches = batches.slice(0, factsRoom)
  }
  if (batches.length) {
    await runBatches('facts', batches, false, b =>
      `你是事实核实员（facts）。对下面每条发现，亲自打开一手证据核实它描述的事实是否成立：引文是否真的在那个位置、规则原句是否真的这么写、` +
      `数字类用脚本重算、行为类在 ${A.scratch}/facts/ 里自己重跑。只凭发现者的转述不算核实。默认 keep=false：证据不足就判不成立。` +
      `\n不看别的核实员的结论。被审材料在 ${D}/材料/（先读 ${README}），只打开和这几条有关的部分。\n待核实（${b.length} 条）：\n${JSON.stringify(b.map(forVerifier))}` +
      `\n每条返回一个 verdict（finding_id 用上面的 id），reason ≤ 3 句并引一手证据，evidence_checked 写打开了哪个文件哪一行 / 跑了什么。` +
      `\n${TIER_BLOCK}`, 1 + batches.length)
  }
  const kept = toVerify.filter(i => verdictIndex[i.id] && verdictIndex[i.id].facts && verdictIndex[i.id].facts.keep === true)
  log(`facts：${kept.length} 条成立，${incomplete.size} 条票没收齐（不算驳回）`)

  if (kept.length) {
    const room = Math.max(0, left() - 1)                 // 留 judge
    let mb = chunk(kept, BATCH)
    if (mb.length > room) {
      const over = mb.slice(room).flat()
      log(`⚠️ 预算只够 ${room} 个 marker 批次，${over.length} 条记为 incomplete：${over.map(i => i.id).join('、')}`)
      over.forEach(i => incomplete.add(i.id))
      mb = mb.slice(0, room)
    }
    if (mb.length) {
      await runBatches('marker', mb, !GUIDED, b =>
        `你是阅卷判断员（marker）。下面这些发现的事实已经核实成立。假设它们属实：照 rubric / spec / 官方答疑，阅卷人会不会因此扣分？扣多少（marks_at_risk_adjusted）？概率多大（p_deduct）？` +
        `没有规则支撑的推测一律判不扣（keep=false）。severity_adjusted 写你认为的真实级别。` +
        (GUIDED ? '' :
          `\n同时做改法安全检查：old_text 是否恰好出现一次；改后是否仍然回答 spec 那一句；结论是否仍然明确、没有被对冲掉；是否没有变长；是否和别处不矛盾；` +
          `代码 / 标记改完是否仍能编译运行（在 ${A.scratch}/marker/ 的副本里试）。不安全就 fix_is_safe=false 并写 better_fix。`) +
        `\n被审材料在 ${D}/材料/（先读 ${README}），只打开和这几条有关的部分。\n待判断（${b.length} 条）：\n${JSON.stringify(b.map(forVerifier))}` +
        `\n${TIER_BLOCK}`, 1)
    }
  }
}

// ============================================================ Judge（主模型）
phase('Judge')
// guided：判官只看到数值和结论，看不到核实员写的理由和证据（那里最容易带出答案）
const verdictsForJudge = Object.create(null)
Object.keys(verdictIndex).forEach(id => {
  verdictsForJudge[id] = {}
  ;['facts', 'marker'].forEach(k => {
    const v = verdictIndex[id][k]
    if (!v) return
    verdictsForJudge[id][k] = GUIDED
      ? { keep: v.keep, severity_adjusted: v.severity_adjusted, marks_at_risk_adjusted: v.marks_at_risk_adjusted, p_deduct: v.p_deduct }
      : v
  })
})
const itemsForJudge = merged.items.map(i => GUIDED ? forVerifier(i) : i)
const report = `${D}/报告-r${A.round}.md`
const judgeKey = keyOf('judge', JSON.stringify([itemsForJudge, verdictsForJudge, Array.from(incomplete), unverified, missingFinders, merged.coverage, notChecked, xmodelState]))
const judge = await agent(
  `你是这一轮审查的判官（${A.course} ${A.code}，第 ${A.round} 轮，档位 ${A.tier}）。先读 ${README}、${R}/mechanical.json（机械预检，不存在就跳过）、${D}/settled.json（不存在当空）。` +
  `\n合并后的发现：\n${JSON.stringify(itemsForJudge)}` +
  `\n核实结果（facts / marker）：\n${JSON.stringify(verdictsForJudge)}` +
  `\n票没收齐的（incomplete，不算驳回）：${JSON.stringify(Array.from(incomplete))}；未核实的（polish 或超预算）：${JSON.stringify(unverified)}` +
  `\n没跑成的镜头：${JSON.stringify(missingFinders)}；跨模型：${xmodelState}；合并时剔除的：${JSON.stringify(merged.dropped.map(d => ({ source: d.source, reason: d.reason })))}` +
  `\n覆盖（照抄进报告 §6）：${JSON.stringify(merged.coverage || [])}` +
  `\n各镜头没查到的（照抄进报告 §8）：${JSON.stringify(notChecked)}` +
  `\n\n分级规则：` +
  `\n· items 里每一条合并后的发现都要有且只有一个分级，id 用上面的，不许新增。` +
  `\n· must_fix：facts 成立 + marker 会扣 + 级别 zero / deduct（或 risk 且期望损失高）。🔒 每一条 must_fix 你都要亲自复现` +
  (GUIDED ? `（只在 ${A.scratch}/judge/ 里做，报告里只写「复现成功 / 失败」，不写复现出来的值）` : `（打开原文、重跑、重算）`) + `；复现不了就降级。` +
  `\n· should_fix：成立且会扣，但只是 risk / 小分，并且修法便宜。` +
  `\n· leave：成立但阅卷人不会扣，或修法成本高于期望损失 —— 理由必填。raised_by ≥ 2 却判 leave 的，在报告里把「期望损失」和「修法成本等级」并排写，让学生知情地选。` +
  `\n· rejected：facts 不成立。unverified / incomplete 照上面的名单，名单里的不许判 must_fix。` +
  `\n· 排序：expected_loss = marks_at_risk_adjusted × p_deduct（核实员的修正值优先）→ raised_by ≥ 2 或跨模型同时命中优先 → 修法成本低的优先。重复的同一个问题只计一次。` +
  `\n· open_must_fix = items 里 class 为 must_fix 的条数。暂定分只给区间并写 caveat；不给单点分数。不许写「可以交了 / 定稿」这类话，只给客观计数。` +
  `\n· not_covered：本报告判断不了的事，以及文件之外只有学生自己能做的（平台设置、提交后状态、新的官方公告）。operations_checklist：交前只有他本人能做的动作。` +
  `\n\n把完整报告用 Write 写到 ${report}，格式：` +
  `\n# ${A.course} ${A.code} · 交前审查 · 第 ${A.round} 轮` +
  `\n抬头一行，以「档位：」开头：档位 · 类型 · 被审文件和 sha256 前 8 位 · 跑了哪些镜头（sonnet 几个、判官 1 个）· 跨模型：${xmodelState} · 没跑成的镜头` +
  (GUIDED ? `\n紧接一行，原样写：⚠️ 按这份作业的 AI 政策，本报告只指出位置和原因，不含改法。` : '') +
  `\n## 0 现状（一句话、客观：必改几条 · 值得改几条 · 未核实几条 · 票没收齐几条 · 暂定分区间仅供排序${missingFinders.length ? ' · 第一句写明有镜头没跑成、结论不完整' : ''}）` +
  `\n## 1 必改（每条：位置 · 期望损失 · 修法成本等级 / 问题 / 作答原文：「逐字引文」/ 依据（出处` + (GUIDED ? '；规则原句只在出自 spec / 官方答疑 / rubric 时用「」逐字引，出自课件、参考答案、起始代码的只写出处' : ' + 规则原句') + `）/ 为什么扣分 / 判官复现 ✅` +
  (GUIDED ? (HINT ? ' / 想一想：引导问题（不含答案）' : '') : ' / 改法：OLD "…" → NEW "…"') + `）` +
  `\n## 2 值得改  ## 3 留着不改（理由必填）  ## 4 已驳回（一行一条）  ## 5 未核实 / 票没收齐  ## 6 覆盖（查过没问题的）` +
  `\n## 7 跨模型（如果跑了）  ## 8 本报告判断不了的事 + 交前只有你能做的  ## 9 机械预检结果` +
  `\n报告用中文写，作业原文和规则原句保持原语言逐字引用。` +
  (GUIDED ? `\n🔒 最后通读一遍报告：任何一处出现了正确答案、正确的值、改好的句子、代码或推导，删掉再落盘。` : '') +
  `\n对被审文件只读。\n${TIER_BLOCK}` + DURABLE(`${R}/judge.json`, judgeKey, `${report}，以及 ${A.scratch}/judge/ 下的临时文件`),
  Object.assign({ label: 'judge', phase: 'Judge', schema: JUDGE_SCHEMA }, JUDGE_OPTS)).then(r => checked(r, judgeKey, 'judge'))
used += 1

// 判官对账：条数、编号、和核实账目冲突的分级 —— 任何一项对不上都算 mismatch，/审查 收口时明说
let judgeCheck = null
if (judge) {
  const jItems = (judge.items || []).filter(i => i && typeof i.id === 'string')
  const mergedIds = new Set(merged.items.map(i => i.id))
  const judgeIds = new Set(jItems.map(i => i.id))
  const mustCount = jItems.filter(i => i.class === 'must_fix').length
  const unknownIds = jItems.map(i => i.id).filter(id => !mergedIds.has(id))
  const missingIds = merged.items.map(i => i.id).filter(id => !judgeIds.has(id))
  const duplicateIds = jItems.map(i => i.id).filter((id, n, xs) => xs.indexOf(id) !== n)
  const vote = (id, k) => verdictIndex[id] && verdictIndex[id][k]
  // must_fix 只能落在「facts 成立 + marker 会扣」的条目上
  const classConflict = jItems.filter(i => i.class === 'must_fix' && (incomplete.has(i.id) || unverified.includes(i.id)
    || !(vote(i.id, 'facts') && vote(i.id, 'facts').keep === true) || !(vote(i.id, 'marker') && vote(i.id, 'marker').keep === true))).map(i => i.id)
  judgeCheck = {
    open_must_fix: judge.open_must_fix, must_fix_items: mustCount, unknownIds, missingIds, duplicateIds, classConflict,
    mismatch: judge.open_must_fix !== mustCount || unknownIds.length > 0 || missingIds.length > 0 || duplicateIds.length > 0 || classConflict.length > 0,
    report_path: judge.report_path,
  }
  if (judgeCheck.mismatch) log(`⚠️ 判官对账不一致：${JSON.stringify({ unknownIds, missingIds, duplicateIds, classConflict, open_must_fix: judge.open_must_fix, must_fix_items: mustCount })}`)
}

return {
  round: A.round, tier: A.tier, dir: D, report, agentsUsed: used, maxAgents: MAX,
  raw: raw.length, merged: merged.items.length, dropped: merged.dropped.length, droppedLeakIds, renumbered, droppedCoverage,
  knownNumbersUsed: !!KNOWN,
  missingFinders, xmodelState, incomplete: Array.from(incomplete), unverified,
  judge: judgeCheck,
  judgeFailed: !judge,
  // 🔒 guided：盘上的 find / merged / verify 文件可能还带着 finder 原话。/审查 收口时必须先跑 scrub（它也会扫报告）
  postScrubRequired: GUIDED,
}
