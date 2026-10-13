// 离线测 .claude/workflows/assignment-review.js 的编排逻辑：把 agent() 换成按剧本回话的假实现，不花 token。
//   node tests/workflow_harness.mjs
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = readFileSync(join(ROOT, '.claude/workflows/assignment-review.js'), 'utf8').replace('export const meta', 'const meta')
const LIB = JSON.parse(readFileSync(join(ROOT, 'shared/工具/审查镜头库.json'), 'utf8'))
const PY = readFileSync(join(ROOT, 'shared/工具/作业审查.py'), 'utf8')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
let FAILS = 0
const check = (cond, what) => { console.log((cond ? '  ✅ ' : '  ❌ ') + what); if (!cond) FAILS++ }

const lens = keys => keys.map(k => LIB.lenses.find(l => l.key === k))
const baseArgs = over => Object.assign({
  root: '/repo', course: 'algorithms', code: 'A1', round: 1, type: 'calc', tier: 'allowed', tierConfirmed: true, manifestDigest: 'abc123',
  lenses: lens(['spec-compliance', 'rubric-marker', 'per-question-check', 'minimal-form']), scratch: '/tmp/x',
}, over)

// 一条带改法、文字里还漏了答案的发现
const leakyFinding = {
  title: 'Q2 递推式的初始条件不对', severity: 'deduct', marks_at_risk: 2,
  location: { file: 'A1-答案.md', line_start: 12 }, work_quote: 'C(1) = 0', rule_quote: 'C(1) = 1 by definition',
  rule_source: { doc: 'slides w02-L1', locator: 'p.8', authority: 'slides' },
  what_is_wrong: '初始条件应该是 C(1) = 1', why_it_costs: '后面的闭式解全错', evidence: ['读了 A1-答案.md:12'], confidence: 0.9,
  suggested_fix: '改成 C(1)=1', old_text: 'C(1) = 0', new_text: 'C(1) = 1', adds_lines: false,
}
const cleanFinding = Object.assign({}, leakyFinding, {
  title: 'Q3 没写 n₀', what_is_wrong: 'Big-O 证明没有给出 n₀ 的具体值', why_it_costs: '课件要求显式给出两个常数',
  work_quote: 'choose c = 6', location: { file: 'A1-答案.md', line_start: 20 },
})

async function run(argsIn, script = {}) {
  const calls = []
  const logs = []
  const finderRuns = {}
  const agent = async (prompt, opts = {}) => {
    calls.push({ label: opts.label, model: opts.model, schema: opts.schema, prompt })
    const label = opts.label || ''
    const key = (prompt.match(/input_key (?:等于|填) "([^"]+)"/) || [])[1]
    const r = await reply(prompt, label)
    if (r && typeof r === 'object') r.input_key = script.staleKey === label || (script.noKeyOnAbort && r.aborted) ? 'old-key' : key
    return r
  }
  const reply = async (prompt, label) => {
    if (label.startsWith('find:') || label === 'xmodel-runner') {
      const key = label === 'xmodel-runner' ? 'xmodel' : label.slice(5)
      finderRuns[key] = (finderRuns[key] || 0) + 1
      if (script.failFinder && script.failFinder[key] >= finderRuns[key]) return null
      if (script.badFinder && script.badFinder[key]) return Object.assign({ lens: key, findings: [], checked_ok: [], not_checked: [] }, script.badFinder[key])
      if (script.hashMismatch === key && (finderRuns[key] > (script.hashMismatchAfter || 0))) return { lens: key, aborted: script.abortText || 'hash-mismatch', findings: [], checked_ok: [], not_checked: [] }
      const findings = script.findings ? (script.findings[key] || []) : key === 'per-question-check' ? [leakyFinding] : key === 'minimal-form' ? [cleanFinding] : []
      return { lens: key, findings: JSON.parse(JSON.stringify(findings)), checked_ok: [`${key} ok`], not_checked: [`${key} 没查 PDF 排版`] }
    }
    if (label === 'merge') {
      const m = prompt.match(/原始发现（JSON，source 是来源编号）：\n(.*)\n/)
      const raw = JSON.parse(m[1])
      const n = script.manyItems || raw.length
      const items = Array.from({ length: n }, (_, i) => {
        const f = Object.assign({}, raw[i % raw.length])
        delete f.source
        return Object.assign(f, { id: `F${String(i + 1).padStart(2, '0')}`, raised_by: 1, merged_from: [`x#${i}`] })
      })
      if (script.mergeIds) items.forEach((it, i) => { it.id = script.mergeIds[i] || it.id })
      return { items, dropped: [], coverage: ['ok'], missing_finders: [] }
    }
    if (label.startsWith('facts:')) {
      if (script.factsFailOnce && !script._factsFailed) { script._factsFailed = true; return null }
      if (script.factsPartialOnce && !script._factsPartial) {
        script._factsPartial = true
        const ids1 = [...prompt.matchAll(/"id":"([^"]+)"/g)].map(x => x[1]).slice(0, 1)
        return { verdicts: ids1.map(id => ({ finding_id: id, keep: true, reason: '核过', evidence_checked: ['x'] })) }
      }
      if (script.factsReject) {
        const ids0 = [...prompt.matchAll(/"id":"([^"]+)"/g)].map(x => x[1])
        return { verdicts: ids0.map(id => ({ finding_id: id, keep: false, reason: '不成立', evidence_checked: ['x'] })) }
      }
      const ids = [...prompt.matchAll(/"id":"([^"]+)"/g)].map(x => x[1])
      return { verdicts: ids.map(id => ({ finding_id: id, keep: true, reason: script.factsReason || '核过', evidence_checked: ['x'] })) }
    }
    if (label.startsWith('marker:')) {
      let ids = [...prompt.matchAll(/"id":"([^"]+)"/g)].map(x => x[1])
      if (script.markerPartial) ids = ids.slice(0, 1)
      return { verdicts: ids.map(id => ({ finding_id: id, keep: true, reason: '会扣', evidence_checked: ['x'], p_deduct: 0.8 })) }
    }
    if (label === 'judge') {
      if (script.judge) return JSON.parse(JSON.stringify(script.judge))
      const ids = [...new Set([...prompt.split('核实结果')[0].matchAll(/"id":"([^"]+)"/g)].map(x => x[1]))]
      return { items: ids.map((id, i) => ({ id, class: i === 0 ? 'must_fix' : 'should_fix', note: 'x' })), open_must_fix: ids.length ? 1 : 0, not_covered: [], operations_checklist: [], report_path: '/repo/x.md' }
    }
    return null
  }
  const parallel = async thunks => Promise.all(thunks.map(t => t().catch(() => null)))
  const fn = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', SRC)
  let result, error
  try {
    result = await fn(agent, parallel, null, () => {}, m => logs.push(m), argsIn, { total: null, spent: () => 0, remaining: () => Infinity })
  } catch (e) { error = e }
  return { result, error, calls, logs }
}

console.log('【闸门】')
{
  const r = await run(baseArgs({ tier: 'forbidden' }))
  check(r.error && /forbidden/.test(r.error.message) && r.calls.length === 0, 'forbidden 档拒跑，一个 agent 都不起')
}
{
  const r = await run(baseArgs({ tierConfirmed: false }))
  check(r.error && /确认/.test(r.error.message) && r.calls.length === 0, '档位没确认就拒跑')
}
{
  const many = lens(['spec-compliance', 'rubric-marker', 'per-question-check', 'minimal-form', 'adversary', 'regression', 'declaration'])
  const r = await run(baseArgs({ lenses: many }))
  check(r.error && /largeApproved/.test(r.error.message) && r.calls.length === 0, '计划超过 10 个 agent 拒跑（要大规模同意）')
  const r2 = await run(baseArgs({ lenses: many, largeApproved: true, maxAgents: 12, dryRun: true }))
  check(!r2.error && r2.result.plannedAgents === 11, '给了 largeApproved 并调高 maxAgents 才放行')
  const r3 = await run(baseArgs({ lenses: many, maxAgents: 50, dryRun: true }))
  check(r3.error && /largeApproved/.test(r3.error.message), '只调高 maxAgents、不给 largeApproved —— 仍然按 10 卡住')
}
{
  const r = await run(baseArgs({ dryRun: true }))
  check(!r.error && r.result.dryRun && r.calls.length === 0 && r.result.plannedAgents === 8, 'dryRun 只返回计划，不起 agent（4 镜头 → 计划 8 个）')
}
{
  const r = await run(baseArgs({ tier: 'guided', lenses: lens(['per-question-check', 'blind-solve']), dryRun: true }))
  check(r.result.finders.join() === 'per-question-check' && r.result.skippedLenses.join() === 'blind-solve', 'guided 档自动去掉 blind-solve（它会在盘上生成完整解答）')
}

console.log('【allowed 档全流程】')
{
  const r = await run(baseArgs())
  check(!r.error, `跑完没有报错${r.error ? '：' + r.error.message : ''}`)
  const nonJudge = r.calls.filter(c => c.label !== 'judge')
  check(nonJudge.every(c => c.model === 'sonnet'), '除判官外每个 agent 都显式用 sonnet')
  check(r.calls.find(c => c.label === 'judge').model === undefined, '判官不写 model（继承主模型）')
  check(r.calls.length <= 10, `总 agent 数 ${r.calls.length} ≤ 10`)
  const findSchema = r.calls.find(c => c.label === 'find:minimal-form').schema
  check('new_text' in findSchema.properties.findings.items.properties, 'allowed 档的 finding schema 有改法字段')
  check('fix_is_safe' in r.calls.find(c => c.label === 'marker:b1').schema.properties.verdicts.items.properties, 'allowed 档的 marker 做改法安全检查')
  check(r.calls.every(c => c.label === 'judge' || /先看 .*\.json/.test(c.prompt)), '每个扇出 agent 都带「先落盘再返回」')
}

console.log('【guided 档：四层防泄漏里的前三层】')
{
  const r = await run(baseArgs({ tier: 'guided' }))
  check(!r.error, `跑完没有报错${r.error ? '：' + r.error.message : ''}`)
  const fs = r.calls.find(c => c.label === 'find:per-question-check').schema.properties.findings.items
  check(!('new_text' in fs.properties) && !('suggested_fix' in fs.properties) && fs.additionalProperties === false, '第 1 层：schema 里根本没有改法字段')
  check(/不许写出正确的值/.test(r.calls.find(c => c.label === 'find:per-question-check').prompt), '第 2 层：prompt 写明不许给答案')
  const mergePrompt = r.calls.find(c => c.label === 'merge').prompt
  check(!/new_text|old_text|suggested_fix/.test(mergePrompt), '第 3 层：finder 偷塞的改法字段在交给 merge 前被剥掉')
  check(/"leak_suspect":true/.test(mergePrompt), '第 3 层：「应该是 …」被标成疑似泄漏')
  check(r.result.dropped >= 1 && r.result.merged === 1, 'merge 之后仍然泄漏的那条被丢弃，干净的那条保留')
  check(!('fix_is_safe' in r.calls.find(c => c.label === 'marker:b1').schema.properties.verdicts.items.properties), 'guided 档的 marker 不做改法检查')
  check(/不含改法/.test(r.calls.find(c => c.label === 'judge').prompt), '第 4 层：判官报告抬头写明「不含改法」并通读删答案')
}

console.log('【缺票与预算】')
{
  const r = await run(baseArgs(), { failFinder: { 'rubric-marker': 1, 'spec-compliance': 9 } })
  check(r.calls.filter(c => c.label === 'find:rubric-marker').length === 2, '没结果的 finder 重跑一次')
  check(r.result.missingFinders.join() === 'spec-compliance', '重跑仍失败的记进 missingFinders，不静默')
  check(r.logs.some(l => /仍然缺/.test(l)), '日志里明说缺了哪个镜头')
}
{
  const r = await run(baseArgs({ maxAgents: 8 }), { manyItems: 25 })
  const factsCalls = r.calls.filter(c => (c.label || '').startsWith('facts:')).length
  check(r.calls.length <= 8, `预算 8：实际起了 ${r.calls.length} 个`)
  check(factsCalls === 1 && r.result.unverified.length === 15, `facts 只排得下 1 批（10 条），其余 15 条记为 unverified`)
  check(r.logs.some(l => /预算只够/.test(l)), '超预算没进核实的，日志里逐条点名')
}

console.log('【审查补充：预算 · 降级 · 账目 · 缓存键】')
{
  const six = lens(['spec-compliance', 'rubric-marker', 'per-question-check', 'minimal-form', 'adversary', 'declaration'])
  const r = await run(baseArgs({ lenses: six.slice(0, 6), largeApproved: true, maxAgents: 10 }), { failFinder: { 'rubric-marker': 1 } })
  check(r.error || r.calls.length <= 10, `6 镜头 + 1 个重跑：总共 ${r.calls.length} 个，不超过 10`)
}
{
  const r = await run(baseArgs(), { hashMismatch: 'rubric-marker' })
  check(r.error && /被改过/.test(r.error.message) && !r.calls.some(c => c.label === 'judge'), '有 finder 报 hash-mismatch → 整轮停下，不起判官')
}
{
  const core = t => LIB.lenses.filter(l => (l.core || []).includes(t))
  for (const t of Object.keys(LIB.types)) {
    for (const xm of [false, true]) {
      const r = await run(baseArgs({ type: t, lenses: core(t), xmodel: xm, dryRun: true }))
      check(!r.error && r.result.plannedAgents <= 10, `${t}${xm ? ' + xmodel' : ''} 的核心镜头：计划 ${r.error ? '报错' : r.result.plannedAgents} 个 ≤ 10`)
    }
  }
}
{
  const r = await run(baseArgs({ xmodel: true }), { failFinder: { xmodel: 9 } })
  check(r.result && r.result.missingFinders.includes('xmodel') && r.result.xmodelState === 'failed', 'xmodel 没结果 → 记进 missingFinders，xmodelState=failed')
  const r2 = await run(baseArgs({ xmodel: { cmd: 'other' } }))
  check(r2.error && /xmodel/.test(r2.error.message), 'xmodel 传对象（没实现的写法）→ 直接报错，不静默当成 false')
}
{
  const ansEv = Object.assign({}, cleanFinding, { title: 'Q4 结果不对', work_quote: 'C(n) = n', evidence: ['重算得到 the answer is n log n'] })
  const r = await run(baseArgs({ tier: 'guided' }), { findings: { 'minimal-form': [cleanFinding, ansEv] }, factsReason: '重算后正确答案是 24,000 bit/s' })
  const judgeP = r.calls.find(c => c.label === 'judge').prompt
  check(/"leak_suspect":true/.test(r.calls.find(c => c.label === 'merge').prompt.split('原始发现')[1].split('\n')[1]), 'evidence 里带答案 → 也会被标成疑似泄漏')
  check(!/24,000/.test(judgeP) && !/重算后/.test(judgeP), 'guided：facts 的 reason 带答案 → 判官根本看不到 reason')
  check(/【档位 guided】/.test(judgeP), 'guided：判官 prompt 里带档位约束')
  check(/覆盖（照抄进报告 §6）/.test(judgeP) && /没查 PDF 排版/.test(judgeP), '覆盖和各镜头没查到的，都交给判官写进报告')
}
{
  const r = await run(baseArgs(), { markerPartial: true, findings: { 'minimal-form': [cleanFinding, Object.assign({}, cleanFinding, { title: 'B', work_quote: 'choose n0', location: { file: 'A1-答案.md', line_start: 30 } })] } })
  check(r.result.incomplete.length === 1, 'marker 只回了一半 → 缺的那条记 incomplete，不当成通过或驳回')
}
{
  const two = [cleanFinding, Object.assign({}, cleanFinding, { title: 'B', work_quote: 'choose n0', location: { file: 'A1-答案.md', line_start: 30 } })]
  const r = await run(baseArgs(), { findings: { 'minimal-form': two }, mergeIds: ['constructor', 'constructor'] })
  check(!r.error && r.logs.some(l => /重新编号/.test(l)), 'merge 给出重复的 / "constructor" 编号 → 不崩，按顺序重编')
}
{
  const a = await run(baseArgs({ manifestDigest: 'aaa' }))
  const b = await run(baseArgs({ manifestDigest: 'bbb' }))
  const keyOf = r => (r.calls.find(c => c.label === 'find:minimal-form').prompt.match(/input_key 等于 "([^"]+)"/) || [])[1]
  check(keyOf(a) && keyOf(a) !== keyOf(b), '材料变了（manifestDigest 不同）→ finder 的缓存键跟着变')
  const g = await run(baseArgs({ tier: 'guided', manifestDigest: 'aaa' }))
  check(keyOf(a) !== keyOf(g), '换了档位 → 缓存键也变（不会复用 allowed 档的产物）')
}
{
  const r = await run(baseArgs(), { failFinder: { 'spec-compliance': 9, 'rubric-marker': 9, 'per-question-check': 9, 'minimal-form': 9 } })
  check(r.error && /都没跑成/.test(r.error.message) && !r.calls.some(c => c.label === 'judge'), '所有镜头都没跑成 → 报错，不出一份看起来干净的空报告')
}
{
  const r = await run(baseArgs())
  check(r.result.judge && r.result.judge.mismatch === false && r.result.judge.must_fix_items === 1, '判官的 open_must_fix 和 items 对账')
}

console.log('【第二轮复核补的】')
{
  const r = await run(baseArgs(), { failFinder: { 'rubric-marker': 1 }, hashMismatch: 'rubric-marker', hashMismatchAfter: 1 })
  check(r.error && /被改过/.test(r.error.message) && !r.calls.some(c => c.label === 'judge'), '重跑时才报 hash-mismatch → 同样整轮停下')
}
{
  const r = await run(baseArgs(), { judge: { items: [{ id: 'F01', class: 'must_fix', note: 'x' }, { id: 'F77', class: 'should_fix', note: 'x' }], open_must_fix: 1, not_covered: [], operations_checklist: [], report_path: '/r' } })
  check(r.result.judge.mismatch === true && r.result.judge.unknownIds.join() === 'F77' && r.result.judge.missingIds.join() === 'F02', '判官给了不存在的编号、漏了一条 → mismatch，并列出是哪几个')
}
{
  const six = lens(['spec-compliance', 'rubric-marker', 'per-question-check', 'minimal-form', 'adversary', 'declaration'])
  const r = await run(baseArgs({ lenses: six, largeApproved: true, maxAgents: 12 }), { factsFailOnce: true })
  check(r.calls.some(c => (c.label || '').startsWith('marker:')), 'facts 重跑过一次之后，marker 仍然有名额')
}
{
  const ansCov = { checked_ok: ['Q3 正确答案是 2n log2 n'], not_checked: ['Q5 应该写成 O(n log n)'] }
  const r = await run(baseArgs({ tier: 'guided' }), { badFinder: { 'rubric-marker': ansCov } })
  const jp = r.calls.find(c => c.label === 'judge').prompt
  check(!/2n log2 n|O\(n log n\)/.test(jp), 'guided：checked_ok / not_checked 里带答案 → 不进判官 prompt')
}
{
  const launder = Object.assign({}, cleanFinding, { title: 'Q1 单位', what_is_wrong: '应该是 datagram', rule_quote: '应该是 datagram' })
  const r = await run(baseArgs({ tier: 'guided' }), { findings: { 'minimal-form': [launder] } })
  check(/"leak_suspect":true/.test(r.calls.find(c => c.label === 'merge').prompt), 'rule_quote 写成和泄漏句一样 → 仍然标疑似泄漏（不能拿 rule_quote 洗白）')
}
{
  const num = Object.assign({}, cleanFinding, { what_is_wrong: '算出来是 24000 bit/s，和作答不符' })
  const r = await run(baseArgs({ tier: 'guided', knownNumbers: ['4000'] }), { findings: { 'minimal-form': [num] } })
  check(/"leak_suspect":true/.test(r.calls.find(c => c.label === 'merge').prompt), 'guided + knownNumbers：作答和规则里都没有的新数字 → 标疑似泄漏')
}
{
  const a = await run(baseArgs({ manifestDigest: 'aaa' }))
  const b = await run(baseArgs({ manifestDigest: 'bbb' }))
  const k = (r, lab) => (r.calls.find(c => c.label === lab).prompt.match(/input_key 等于 "([^"]+)"/) || [])[1]
  check(k(a, 'merge') !== k(b, 'merge') && k(a, 'facts:b1') !== k(b, 'facts:b1') && k(a, 'judge') !== k(b, 'judge'), 'merge / facts / judge 的缓存键也带 manifestDigest')
}
{
  const r = await run(baseArgs(), { staleKey: 'find:minimal-form' })
  check(r.calls.filter(c => c.label === 'find:minimal-form').length === 2 && r.logs.some(l => /input_key/.test(l)), '返回的 input_key 不是这次的（读了旧文件）→ 当作没结果，重跑')
}
{
  const two = [cleanFinding, Object.assign({}, cleanFinding, { title: 'B', work_quote: 'choose n0', location: { file: 'A1-答案.md', line_start: 30 } })]
  const r = await run(baseArgs(), { findings: { 'minimal-form': two }, mergeIds: ['F01', 'F01'] })
  check(r.result.renumbered === true, '重编号会写进返回值（盘上的 merged.json 由 scrub / assemble 按同样规则重编）')
}
{
  const grab = (src, name, flags) => (src.match(new RegExp(flags ? `const ${name} = /(.*)/${flags}\n` : `const ${name} = /(.*)/\n`)) || [])[1]
  const grabPy = name => ((PY.match(new RegExp(`${name} = re\\.compile\\(r"(.*)"(, re\\.[IA]( \\| re\\.[IA])?)?\\)\n`)) || [])[1] || '').replace(/\\"/g, '"')
  check(grab(SRC, 'NUM', '') === grabPy('NUM') && grab(SRC, 'STRUCT', 'i') === grabPy('STRUCT'), 'NUM / STRUCT 两边一字不差')
}

console.log('【第三轮复核补的】')
{
  for (const t of ['hash-mismatch: MANIFEST differs', 'HASH-MISMATCH']) {
    const r = await run(baseArgs(), { hashMismatch: 'rubric-marker', abortText: t, noKeyOnAbort: true })
    check(r.error && /被改过/.test(r.error.message), `aborted="${t}" 且没带对的 input_key → 仍然整轮停下`)
  }
  const r = await run(baseArgs(), { hashMismatch: 'rubric-marker', abortText: 'error: tool budget exhausted' })
  check(!r.error && r.result.missingFinders.includes('rubric-marker'), '其他中止（工具额度用完）→ 算没跑成，不当成零条发现')
}
{
  const r = await run(baseArgs(), { factsPartialOnce: true, findings: { 'minimal-form': [cleanFinding, Object.assign({}, cleanFinding, { title: 'B', work_quote: 'choose n0', location: { file: 'A1-答案.md', line_start: 30 } })] } })
  const redo = r.calls.filter(c => c.label === 'facts:b1')[1]
  check(redo && /这是重做/.test(redo.prompt) && !/直接读出来原样返回/.test(redo.prompt), '重做的 prompt 不再叫 agent 读回旧文件（不然会原样返回残缺结果）')
  check(r.result.incomplete.length === 0, '↑ 重做补齐了票')
}
{
  const items = Array.from({ length: 12 }, (_, i) => Object.assign({}, cleanFinding, { title: `T${i}`, work_quote: `q${i}`, location: { file: 'A1-答案.md', line_start: 10 * i } }))
  const r = await run(baseArgs({ lenses: lens(['spec-compliance', 'minimal-form']), verifyBatch: 6 }), { findings: { 'minimal-form': items }, factsPartialOnce: true })
  check(r.calls.filter(c => (c.label || '').startsWith('marker:')).length === 2, 'facts 有一批要重做时，先保证 marker 的批次和判官有名额')
}
{
  const r = await run(baseArgs(), { judge: { items: [{ id: 'F01', class: 'must_fix', note: 'x' }, { id: 'F01', class: 'leave', note: 'x' }, { id: 'F02', class: 'leave', note: 'x' }], open_must_fix: 1, not_covered: [], operations_checklist: [], report_path: '/r' } })
  check(r.result.judge.mismatch === true && r.result.judge.duplicateIds.join() === 'F01', '判官给同一编号两个分级 → mismatch')
  const r2 = await run(baseArgs(), { factsReject: true, judge: { items: [{ id: 'F01', class: 'must_fix', note: 'x' }, { id: 'F02', class: 'rejected', note: 'x' }], open_must_fix: 1, not_covered: [], operations_checklist: [], report_path: '/r' } })
  check(r2.result.judge.mismatch === true && r2.result.judge.classConflict.join() === 'F01', 'facts 判不成立的条目被判成 must_fix → mismatch')
}
{
  const r = await run(baseArgs({ verifyBatch: -1 }))
  check(r.error && /verifyBatch/.test(r.error.message) && r.calls.length === 0, 'verifyBatch 不是正整数 → 直接报错（不会死循环）')
}
{
  const r = await run(baseArgs({ xmodel: true }), { failFinder: { xmodel: 1 } })
  check(r.calls.filter(c => c.label === 'xmodel-runner').length === 1 && r.result.xmodelState === 'failed', 'xmodel 失败不重跑（每份作业 Codex 次数有上限）')
}
{
  const ev = Object.assign({}, cleanFinding, { evidence: ['/tmp/x/minimal-form/run-55512345.log', '读了 A1-答案.md:150'] })
  const r = await run(baseArgs({ tier: 'guided', knownNumbers: ['4000'] }), { findings: { 'minimal-form': [ev] } })
  check(!/"leak_suspect":true/.test(r.calls.find(c => c.label === 'merge').prompt), 'scratch 路径和 文件:行 里的数字不算「新数字」')
  check(r.result.knownNumbersUsed === true, '返回值写明有没有用上 knownNumbers')
}

console.log(`== 失败 ${FAILS} 项`)
process.exit(FAILS ? 1 : 0)
