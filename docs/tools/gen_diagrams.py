"""重新生成 docs/assets/ 下的五张架构图（亮/暗两套配色）。

用法：python3 docs/tools/gen_diagrams.py docs/assets
预览：rsvg-convert docs/assets/architecture.svg -o /tmp/a.png（亮色）；暗色用浏览器的 prefers-color-scheme。
"""
import os, sys, html

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

CSS = """
text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC","Noto Sans SC",sans-serif}
.mono{font-family:"SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono","PingFang SC","Microsoft YaHei",monospace}
.bg{fill:#ffffff;stroke:#d1d9e0}
.band{fill:#f6f8fa;stroke:#d1d9e0}
.card{fill:#ffffff;stroke:#d1d9e0}
.t{fill:#1f2328}.m{fill:#59636e}.w{fill:#ffffff}
.ln{stroke:#8c959f;fill:none}.ahf{fill:#8c959f}
.k-blue{stroke:#0969da}.f-blue{fill:#0969da}.s-blue{fill:#ddf4ff}.c-blue{fill:#0969da}
.k-orange{stroke:#bc4c00}.f-orange{fill:#bc4c00}.s-orange{fill:#fff1e5}.c-orange{fill:#bc4c00}
.k-green{stroke:#1a7f37}.f-green{fill:#1a7f37}.s-green{fill:#dafbe1}.c-green{fill:#1a7f37}
.k-red{stroke:#cf222e}.f-red{fill:#cf222e}.s-red{fill:#ffebe9}.c-red{fill:#cf222e}
.k-purple{stroke:#8250df}.f-purple{fill:#8250df}.s-purple{fill:#fbefff}.c-purple{fill:#8250df}
.k-gray{stroke:#818b98}.f-gray{fill:#59636e}.s-gray{fill:#f6f8fa}.c-gray{fill:#59636e}
.g0{stop-color:#f6f8fa}.g1{stop-color:#eaf2ff}
.slide{fill:#ffffff;stroke:#d1d9e0}.ph{fill:#d1d9e0}
@media (prefers-color-scheme: dark){
.bg{fill:#0d1117;stroke:#3d444d}
.band{fill:#151b23;stroke:#3d444d}
.card{fill:#0d1117;stroke:#3d444d}
.t{fill:#e6edf3}.m{fill:#9198a1}
.ln{stroke:#656c76}.ahf{fill:#656c76}
.k-blue{stroke:#4493f8}.f-blue{fill:#1f6feb}.s-blue{fill:rgba(56,139,253,.15)}.c-blue{fill:#4493f8}
.k-orange{stroke:#f0883e}.f-orange{fill:#bd561d}.s-orange{fill:rgba(219,109,40,.15)}.c-orange{fill:#f0883e}
.k-green{stroke:#3fb950}.f-green{fill:#238636}.s-green{fill:rgba(46,160,67,.15)}.c-green{fill:#3fb950}
.k-red{stroke:#f85149}.f-red{fill:#da3633}.s-red{fill:rgba(248,81,73,.15)}.c-red{fill:#f85149}
.k-purple{stroke:#ab7df8}.f-purple{fill:#8957e5}.s-purple{fill:rgba(171,125,248,.15)}.c-purple{fill:#ab7df8}
.k-gray{stroke:#656c76}.f-gray{fill:#6e7681}.s-gray{fill:rgba(110,118,129,.12)}.c-gray{fill:#9198a1}
.g0{stop-color:#0d1117}.g1{stop-color:#132039}
.slide{fill:#151b23;stroke:#3d444d}.ph{fill:#3d444d}
}
"""

COLORS = ["blue", "orange", "green", "red", "purple", "gray"]
WARN = []


def esc(s):
    return html.escape(s, quote=False)


def tw(s, size, mono=False):
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o > 0x2E80:
            w += size * 1.0
        elif 0x2000 <= o < 0x2C00:
            w += size * 0.95
        elif mono:
            w += size * 0.6
        elif ch in "ilI.,:;|!'·":
            w += size * 0.3
        elif ch.isupper() or ch in "mwMW@":
            w += size * 0.68
        elif ch == " ":
            w += size * 0.3
        else:
            w += size * 0.56
    return w


class SVG:
    def __init__(self, w, h, title):
        self.w, self.h, self.title = w, h, title
        self.parts = []

    def add(self, s):
        self.parts.append(s)

    def rect(self, x, y, w, h, cls, rx=10, extra=""):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" class="{cls}" {extra}/>')

    def text(self, x, y, s, cls="t", size=14, weight=400, anchor="start", mono=False, maxw=None):
        if maxw is not None and tw(s, size, mono) > maxw:
            WARN.append(f"{self.title}: 文本可能溢出（{tw(s, size, mono):.0f} > {maxw}）：{s}")
        c = cls + (" mono" if mono else "")
        self.add(f'<text x="{x}" y="{y}" class="{c}" font-size="{size}" font-weight="{weight}" '
                 f'text-anchor="{anchor}">{esc(s)}</text>')

    def chip(self, x, y, s, color, size=12, mono=False, h=22):
        w = tw(s, size, mono) + 18
        self.rect(x, y, w, h, f"f-{color}", rx=h / 2)
        self.text(x + w / 2, y + h / 2 + size * 0.36, s, "w", size, 600, "middle", mono)
        return w

    def line(self, pts, cls="ln", dashed=False, arrow=True, color=None, width=1.6):
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        k = f"k-{color}" if color else "ln"
        mk = f'marker-end="url(#ah-{color or "n"})"' if arrow else ""
        da = 'stroke-dasharray="6 5"' if dashed else ""
        self.add(f'<path d="{d}" class="{k}" fill="none" stroke-width="{width}" {da} {mk}/>')

    def path(self, d, color=None, dashed=False, arrow=True, width=1.6):
        k = f"k-{color}" if color else "ln"
        mk = f'marker-end="url(#ah-{color or "n"})"' if arrow else ""
        da = 'stroke-dasharray="6 5"' if dashed else ""
        self.add(f'<path d="{d}" class="{k}" fill="none" stroke-width="{width}" {da} {mk}/>')

    def render(self, desc=""):
        markers = "".join(
            f'<marker id="ah-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" class="{"ahf" if c == "n" else "c-" + c}"/></marker>'
            for c in COLORS + ["n"])
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.title)}">\n'
                f'<title>{esc(self.title)}</title><desc>{esc(desc)}</desc>\n'
                f'<style>{CSS}</style>\n<defs>{markers}'
                f'<linearGradient id="hero" x1="0" y1="0" x2="1" y2="1"><stop offset="0" class="g0"/><stop offset="1" class="g1"/></linearGradient>'
                f'</defs>\n' + "\n".join(self.parts) + "\n</svg>\n")

    def save(self, name, desc=""):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(self.render(desc))


def card(s, x, y, w, h, color, accent=True):
    s.rect(x, y, w, h, "card", rx=10, extra='stroke-width="1"')
    if accent:
        s.rect(x + 1, y + 12, 4, h - 24, f"f-{color}", rx=2)


# ====================================================================== banner
def banner():
    s = SVG(1280, 300, "claude-study-system —— 让 Claude Code 带着你一页一页学课件")
    s.rect(0.5, 0.5, 1279, 299, "bg", rx=18)
    s.add('<rect x="0.5" y="0.5" width="1279" height="299" rx="18" fill="url(#hero)"/>')
    s.chip(64, 58, "Claude Code · 学习架构模板", "blue", 13)
    s.text(64, 140, "claude-study-system", "t", 54, 700)
    s.text(66, 186, "让 Claude Code 带着你一页一页学课件", "t", 26, 600)
    s.text(66, 226, "规则写进 CLAUDE.md · 状态落进账本 · hook 把「Claude 记得」变成「机械拦住」", "m", 16)
    # 右侧：一叠课件 + 「讲·答·批」徽章
    bx, by = 900, 52
    s.rect(bx + 44, by + 6, 250, 170, "slide", rx=10, extra='opacity=".55"')
    s.rect(bx + 22, by + 20, 250, 170, "slide", rx=10, extra='opacity=".8"')
    fx, fy = bx, by + 36
    s.rect(fx, fy, 250, 170, "slide", rx=10)
    s.rect(fx + 20, fy + 20, 120, 12, "f-blue", rx=4)
    for i, ww in enumerate([200, 170, 185]):
        s.rect(fx + 20, fy + 48 + i * 18, ww, 8, "ph", rx=4)
    s.rect(fx + 14, fy + 104, 222, 52, "s-orange", rx=8, extra='stroke-width="1.4"')
    s.add(f'<rect x="{fx + 14}" y="{fy + 104}" width="222" height="52" rx="8" fill="none" class="k-orange" stroke-width="1.4" stroke-dasharray="5 4"/>')
    s.text(fx + 26, fy + 135, "一块 ≤ 5 页", "c-orange", 15, 700)
    s.text(fx + 224, fy + 135, "p.1–5", "c-orange", 13, 600, "end", mono=True)
    cx, cy = 1206, 214
    s.add(f'<circle cx="{cx}" cy="{cy}" r="38" class="f-green"/>')
    s.add(f'<path d="M{cx - 16},{cy + 1} l11,11 l21,-23" fill="none" stroke="#ffffff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>')
    s.text(cx, cy + 62, "讲 · 答 · 批", "c-green", 14, 700, "middle")
    s.save("banner.svg", "项目横幅")


# ====================================================================== architecture
def architecture():
    W, H = 1200, 822
    s = SVG(W, H, "claude-study-system 架构总览")
    s.rect(0.5, 0.5, W - 1, H - 1, "bg", rx=16)
    s.text(40, 52, "架构总览", "t", 24, 700)
    s.text(40, 80, "三层：守护层（hooks）把规则机械化 · 会话层（slash commands）一个对话只做一件事 · 状态层（文件）是唯一事实来源", "m", 14)

    # ---- 守护层
    s.rect(24, 104, W - 48, 178, "band", rx=14)
    s.text(44, 130, "守护层", "t", 15, 700)
    s.text(100, 130, ".claude/hooks/ · .claude/settings.json", "m", 13, mono=True)
    gates = [
        ("开场门", "SessionStart", "inject-context.sh → 导航.py", ["注入约 5 KB 导航层：周次 · 近期硬截止", "· 各课状态 · 断点 · 当天提醒 · 体检结果"], "只注入", "gray", "blue"),
        ("消息门", "UserPromptSubmit", "inject-time.sh", ["每条消息注入：当前时间 · 开讲前必查", "对照表 · 批改先贴题干 · 出题全英文"], "只注入", "gray", "blue"),
        ("收尾门", "Stop", "stop-check.sh → 检查.py", ["有硬伤 → exit 2 拦回，先修再结束回合", "全绿 → 行动项审计 → git 快照"], "能拦回合", "red", "red"),
    ]
    for i, (name, ev, script, desc, badge, bc, ac) in enumerate(gates):
        x, y, w, h = 40 + i * 380, 148, 360, 118
        card(s, x, y, w, h, ac)
        s.text(x + 22, y + 32, name, "t", 18, 700)
        s.text(x + 22 + tw(name, 18) + 10, y + 32, ev, "m", 13, mono=True)
        bw = tw(badge, 12) + 18
        s.chip(x + w - bw - 14, y + 14, badge, bc, 12)
        s.text(x + 22, y + 58, script, "c-" + ("red" if ac == "red" else "blue"), 13, 500, mono=True, maxw=w - 40)
        for j, d in enumerate(desc):
            s.text(x + 22, y + 82 + j * 21, d, "t", 13.5, maxw=w - 36)

    # 守护层 ↔ 会话层
    s.line([(330, 282), (330, 330)], color="blue")
    s.text(342, 312, "注入：导航层 · 时间 · 必做清单", "c-blue", 12.5, 600)
    s.line([(890, 330), (890, 282)], color="red")
    s.text(878, 312, "每个回合结束都要过门", "c-red", 12.5, 600, "end")

    # ---- 会话层
    y0 = 330
    s.rect(24, y0, W - 48, 214, "band", rx=14)
    s.text(44, y0 + 26, "会话层", "t", 15, 700)
    s.text(100, y0 + 26, ".claude/commands/ —— 一个对话只做一件事", "m", 13)
    # 你
    card(s, 40, y0 + 44, 160, 154, "gray", accent=False)
    s.text(120, y0 + 82, "你", "t", 22, 700, "middle")
    for j, d in enumerate(["开新对话", "没点名 → 摆菜单", "点了名 → 直接开工"]):
        s.text(120, y0 + 112 + j * 24, d, "m", 13, anchor="middle")
    s.line([(200, y0 + 110), (226, y0 + 110)])
    sess = [
        ("/学 <课程> <deck>", "blue", "讲 → 出题 → 答 → 批 → 记账 → 收尾卷", "写：自己那门课的目录 · 交接单"),
        ("/作业 <课程> <代号>", "orange", "读 spec → 指引 / 检查 / 排版 → 交", "写：自己那门课的目录 · 交接单"),
        ("/总控", "green", "邮箱 · 归档 · 预扫 · 转录 · 看板 · 体检", "写：shared/ 账本 · 脚本 · 规则"),
    ]
    xs = [230, 535, 880]
    for (cmd, c, l1, l2), x in zip(sess, xs):
        w = 280
        card(s, x, y0 + 44, w, 118, c)
        s.chip(x + 20, y0 + 60, cmd, c, 13, mono=True, h=24)
        s.text(x + 20, y0 + 112, l1, "t", 13.5, maxw=w - 30)
        s.text(x + 20, y0 + 138, l2, "m", 13, maxw=w - 30)
    # 交接通道
    s.rect(230, y0 + 172, 585, 30, "s-purple", rx=8)
    s.add(f'<rect x="230" y="{y0 + 172}" width="585" height="30" rx="8" fill="none" class="k-purple" stroke-width="1.2"/>')
    s.text(246, y0 + 192, "shared/交接/", "c-purple", 13, 700, mono=True)
    s.text(350, y0 + 192, "学习会话改不了账本 → 把「该改哪张表哪一行」写成交接单", "c-purple", 13)
    s.line([(370, y0 + 162), (370, y0 + 172)], color="purple", arrow=False)
    s.line([(675, y0 + 162), (675, y0 + 172)], color="purple", arrow=False)
    s.line([(815, y0 + 187), (848, y0 + 187), (848, y0 + 120), (876, y0 + 120)], color="purple", dashed=True)
    s.text(1020, y0 + 192, "开场第 0 步先清交接单", "c-green", 12.5, 600, "middle")

    # 会话层 → 状态层
    s.line([(600, y0 + 214), (600, y0 + 258)])
    s.text(612, y0 + 242, "读写都按「写文件分区」", "m", 12.5, 600)

    # ---- 状态层
    y1 = y0 + 258
    s.rect(24, y1, W - 48, 210, "band", rx=14)
    s.text(44, y1 + 26, "状态层", "t", 15, 700)
    s.text(100, y1 + 26, "文件即状态 —— 新对话从文件取，不靠记忆", "m", 13)
    cols = [
        ("账本 · shared/", "purple", [("lecture台账.md", "唯一数据源"), ("deadlines.md", "日历 + 截止"), ("学习日志.md", "状态 + 断点"),
                                      ("学时记录.md", "学时"), ("公告.md", "考点 / 不考"), ("提醒.tsv · 活动会话.tsv", "")]),
        ("课程 · <课程>/", "blue", [("CLAUDE.md", "考核 · hurdle"), ("课件/ + _预扫/", "页数 · offset"), ("字幕/<deck>-对照.md", "转录索引"),
                                   ("笔记/ · 收尾卷/", ""), ("答题表达套件.md", "英文句式")]),
        ("规则", "orange", [("CLAUDE.md", "常驻规则"), ("shared/规则档案.md", "为什么"), ("shared/工具手册.md", "怎么做"),
                           ("工具/规则同步.tsv", "防副本走样")]),
        ("工具 · shared/工具/", "green", [("shared/配置.json", "唯一要改的"), ("导航.py", "→ 开场门"), ("检查.py", "→ 收尾门"),
                                       ("预扫.py · 文字层索引.py", ""), ("对齐.py · 转录.sh · md2pdf.py", "")]),
    ]
    for i, (head, c, items) in enumerate(cols):
        x, w = 40 + i * 285, 265
        card(s, x, y1 + 42, w, 154, c, accent=False)
        s.chip(x + 14, y1 + 52, head, c, 12.5, mono=False, h=22)
        for j, (a, b) in enumerate(items):
            yy = y1 + 98 + j * 17
            s.text(x + 16, yy, a, "t", 11.5, 500, mono=True, maxw=w - 20 - (tw(b, 11) if b else 0))
            if b:
                s.text(x + w - 14, yy, b, "m", 11, anchor="end")
    s.save("architecture.svg", "守护层、会话层、状态层三层架构")


# ====================================================================== lifecycle
def lifecycle():
    W, H = 1200, 500
    s = SVG(W, H, "一个会话的生命周期")
    s.rect(0.5, 0.5, W - 1, H - 1, "bg", rx=16)
    s.text(40, 52, "一个会话的生命周期", "t", 24, 700)
    s.text(40, 80, "三道门：两道只往上下文里注入，只有收尾门能把回合拦回去", "m", 14)

    y = 112
    nodes = [
        (40, 140, "开新对话", None, "gray"),
        (210, 220, "开场门 · SessionStart", "注入导航层（约 5 KB）", "blue"),
        (460, 210, "第一句点名了吗？", "没有 → 摆菜单让你点", "gray"),
        (700, 220, "进入 /学 · /作业 · /总控", "登记 活动会话.tsv", "blue"),
        (950, 210, "收尾", "交接单 → 登记表翻 done", "green"),
    ]
    for (x, w, a, b, c) in nodes:
        card(s, x, y, w, 70, c, accent=(c != "gray"))
        s.text(x + w / 2, y + (32 if b else 42), a, "t", 14.5, 700, "middle", maxw=w - 16)
        if b:
            s.text(x + w / 2, y + 54, b, "m", 12.5, anchor="middle", maxw=w - 16)
    for (x1, x2) in [(180, 210), (430, 460), (670, 700)]:
        s.line([(x1, y + 35), (x2 - 2, y + 35)])

    # 回合循环
    ly = 222
    s.rect(40, ly, 1120, 250, "band", rx=14)
    s.text(60, ly + 28, "每个回合（循环）", "t", 15, 700)
    s.line([(810, y + 70), (810, ly)], color="blue")
    s.line([(1055, ly), (1055, y + 72)], color="green")
    s.text(1066, ly - 14, "说要收尾", "c-green", 12.5, 600)

    sy = ly + 76
    steps = [
        (64, 128, "你发消息", None, "gray"),
        (222, 236, "消息门 · UserPromptSubmit", "注入时间 · 必做清单 · 硬伤", "blue"),
        (488, 190, "Claude 干活", "讲 · 批 · 改文件", "gray"),
        (708, 196, "收尾门 · Stop", "跑 检查.py（14 项）", "red"),
    ]
    for (x, w, a, b, c) in steps:
        card(s, x, sy, w, 76, c, accent=(c != "gray"))
        s.text(x + w / 2, sy + (32 if b else 44), a, "t", 14, 700, "middle", maxw=w - 14)
        if b:
            s.text(x + w / 2, sy + 56, b, "m", 12.5, anchor="middle", maxw=w - 14)
    for (x1, x2) in [(192, 222), (458, 488), (678, 708)]:
        s.line([(x1, sy + 38), (x2 - 2, sy + 38)])
    # 两个出口
    ox = 940
    s.rect(ox, sy - 22, 200, 54, "s-red", rx=10)
    s.add(f'<rect x="{ox}" y="{sy - 22}" width="200" height="54" rx="10" fill="none" class="k-red" stroke-width="1.3"/>')
    s.text(ox + 100, sy, "有硬伤", "c-red", 14, 700, "middle")
    s.text(ox + 100, sy + 20, "exit 2 拦回，修完再交", "c-red", 12.5, anchor="middle")
    s.rect(ox, sy + 46, 200, 54, "s-green", rx=10)
    s.add(f'<rect x="{ox}" y="{sy + 46}" width="200" height="54" rx="10" fill="none" class="k-green" stroke-width="1.3"/>')
    s.text(ox + 100, sy + 68, "全绿", "c-green", 14, 700, "middle")
    s.text(ox + 100, sy + 88, "行动项审计 → git 快照", "c-green", 12.5, anchor="middle")
    s.line([(904, sy + 30), (920, sy + 30), (920, sy + 5), (ox - 2, sy + 5)], color="red")
    s.line([(920, sy + 30), (920, sy + 73), (ox - 2, sy + 73)], color="green")
    # 拦回 → Claude 干活
    s.path(f"M{ox + 100},{sy - 22} L{ox + 100},{sy - 38} L{583},{sy - 38} L{583},{sy - 2}", color="red", dashed=True)
    s.text(760, sy - 44, "拦回：同一回合里修", "c-red", 12.5, 600, "middle")
    # 全绿 → 下一条消息
    s.path(f"M{ox + 100},{sy + 100} L{ox + 100},{sy + 130} L{128},{sy + 130} L{128},{sy + 78}", color="green", dashed=True)
    s.text(530, sy + 124, "放行：等你的下一条消息", "c-green", 12.5, 600, "middle")
    s.save("session-lifecycle.svg", "开场门、消息门、收尾门在一个会话里的位置")


# ====================================================================== study loop
def study_loop():
    W, H = 1200, 470
    s = SVG(W, H, "学一份课件")
    s.rect(0.5, 0.5, W - 1, H - 1, "bg", rx=16)
    s.text(40, 52, "学一份课件", "t", 24, 700)
    s.text(40, 80, "讲 → 答 → 批，缺一件就重讲 —— 台账只记「三件都齐」的页码", "m", 14)
    steps = [
        ("预扫摘要", "没有就先跑", "预扫.py", "gray"),
        ("校对对照表", "没校对的页", "不许开讲", "gray"),
        ("发课件 PDF", "右侧面板", "左右对照着看", "gray"),
        ("讲一块", "≤ 5 页真内容", "讲完必停", "blue"),
        ("块中快测", "判断 · 选择 · 填空", "不出简答", "orange"),
        ("你答", "英文作答", "", "orange"),
        ("批改", "先原样贴题干", "只展开错的", "orange"),
        ("记账", "台账 + 学时", "同一回合", "green"),
    ]
    y, w, gap, x0 = 112, 126, 20, 40
    for i, (a, b, c2, col) in enumerate(steps):
        x = x0 + i * (w + gap)
        card(s, x, y, w, 112, col, accent=(col != "gray"))
        s.add(f'<circle cx="{x + 24}" cy="{y + 24}" r="12" class="f-{col}"/>')
        s.text(x + 24, y + 29, str(i + 1), "w", 13, 700, "middle")
        s.text(x + 16, y + 64, a, "t", 15, 700, maxw=w - 20)
        s.text(x + 16, y + 86, b, "m", 12.5, maxw=w - 20)
        if c2:
            s.text(x + 16, y + 104, c2, "m", 12.5, maxw=w - 20)
        if i < len(steps) - 1:
            s.line([(x + w + 2, y + 56), (x + w + gap - 3, y + 56)])
    # 下一块
    x4 = x0 + 3 * (w + gap) + w / 2
    x8 = x0 + 7 * (w + gap) + w / 2
    s.path(f"M{x8 - 20},{y + 112} L{x8 - 20},{y + 146} L{x4},{y + 146} L{x4},{y + 116}", color="blue", dashed=True)
    s.text((x4 + x8) / 2, y + 140, "下一块（一个算法 / 机制讲完再出题）", "c-blue", 12.5, 600, "middle")
    # 收尾卷
    s.line([(x8 + 22, y + 112), (x8 + 22, y + 190)], color="purple")
    s.text(x8 + 14, y + 160, "最后一块", "c-purple", 12, 600, "end")

    cy = 304
    # 完成的定义
    card(s, 40, cy, 380, 140, "green")
    s.text(64, cy + 32, "完成的定义", "t", 16, 700)
    cx = 64
    for t, c in [("① 讲了", "blue"), ("② 你答了", "orange"), ("③ 批了", "orange")]:
        cx += s.chip(cx, cy + 50, t, c, 13, h=26) + 10
    s.text(64, cy + 104, "缺一件 → 那一块算没完成，下次从头重讲", "t", 13.5)
    s.text(64, cy + 126, "检查.py 的凭证锁：台账推进必须同回合有学时记录", "m", 12.5)
    # 转录是配料
    card(s, 440, cy, 380, 140, "gray", accent=False)
    s.text(462, cy + 32, "转录只是配料", "t", 16, 700)
    s.text(462, cy + 56, "讲到第 N 页才去转录里定位那一段，当场标：", "m", 13)
    lx = 462
    for t, c in [("考点", "red"), ("不考", "gray"), ("补充", "blue"), ("易错", "orange")]:
        s.add(f'<circle cx="{lx + 7}" cy="{cy + 84}" r="7" class="f-{c}"/>')
        s.text(lx + 20, cy + 89, t, "t", 13.5, 600)
        lx += 82
    s.text(462, cy + 120, "考点 / 不考 同时进 公告.md（考试范围总账）", "m", 12.5)
    # 收尾卷
    card(s, 840, cy, 320, 140, "purple")
    s.text(864, cy + 32, "收尾卷", "t", 16, 700)
    s.text(864, cy + 58, "一讲讲完出一份，像考卷一样", "t", 13.5)
    s.text(864, cy + 82, "含 justify · in about three sentences", "m", 12.5)
    s.text(864, cy + 104, "唯一测「英文写不写得出」的地方", "m", 12.5)
    s.text(864, cy + 126, "→ <课程>/收尾卷/", "c-purple", 12.5, 600, mono=True)
    s.save("study-loop.svg", "学一份课件的八步循环")


# ====================================================================== partition
def partition():
    W, H = 1200, 452
    s = SVG(W, H, "写文件分区")
    s.rect(0.5, 0.5, W - 1, H - 1, "bg", rx=16)
    s.text(40, 52, "写文件分区", "t", 24, 700)
    s.text(40, 80, "学习类和总控类可以同时开 —— 靠「谁能写什么」防互相覆盖，不靠串行", "m", 14)
    panels = [
        (40, "学习类", "/学 · /作业", "blue",
         ["自己那门课的目录 <课程>/", "shared/学时记录.md（只追加明细行）", "shared/交接/（回写账本的唯一通道）", "shared/活动会话.tsv（自己那一行）"],
         ["shared/ 的账本（台账 · 日志 · deadlines · 公告）", "脚本 · hooks · 规则文件"]),
        (720, "总控类", "/总控", "green",
         ["shared/ 的账本 —— 只有它能改", ".claude/ · shared/工具/（架构和脚本）", "各课目录（归档 · 预扫 · 转录）", "学时累计表重算 · 交接/已处理/"],
         ["同时开第二个总控（两个都握账本写权）"]),
    ]
    for (x, name, cmd, c, can, cannot) in panels:
        w = 440
        card(s, x, 108, w, 316, c)
        s.text(x + 26, 144, name, "t", 19, 700)
        s.chip(x + 26 + tw(name, 19) + 14, 127, cmd, c, 13, mono=True, h=24)
        s.text(x + 26, 180, "能写", "c-green", 13, 700)
        for j, t in enumerate(can):
            s.text(x + 26, 206 + j * 24, "✓", "c-green", 14, 700)
            s.text(x + 48, 206 + j * 24, t, "t", 13.5, maxw=w - 60)
        yy = 206 + len(can) * 24 + 14
        s.text(x + 26, yy, "不许写", "c-red", 13, 700)
        for j, t in enumerate(cannot):
            s.text(x + 26, yy + 26 + j * 24, "✗", "c-red", 14, 700)
            s.text(x + 48, yy + 26 + j * 24, t, "t", 13.5, maxw=w - 60)
    # 中间：交接通道
    mx, mw = 500, 200
    s.add(f'<ellipse cx="{mx + mw / 2}" cy="196" rx="70" ry="14" class="s-purple"/>')
    s.add(f'<path d="M{mx + 30},196 v120 a70,14 0 0 0 140,0 v-120" class="s-purple"/>')
    s.add(f'<path d="M{mx + 30},196 v120 a70,14 0 0 0 140,0 v-120" fill="none" class="k-purple" stroke-width="1.4"/>')
    s.add(f'<ellipse cx="{mx + mw / 2}" cy="196" rx="70" ry="14" fill="none" class="k-purple" stroke-width="1.4"/>')
    s.text(mx + mw / 2, 252, "shared/交接/", "c-purple", 14, 700, "middle", mono=True)
    s.text(mx + mw / 2, 276, "交接单", "t", 13.5, 600, "middle")
    s.text(mx + mw / 2, 298, "该改哪张表哪一行", "m", 12, anchor="middle")
    s.line([(480, 238), (528, 238)], color="purple")
    s.line([(672, 238), (716, 238)], color="purple")
    s.text(mx + mw / 2, 366, "学习会话收尾时写", "c-purple", 12.5, 600, "middle")
    s.text(mx + mw / 2, 388, "总控开场第 0 步落账", "c-green", 12.5, 600, "middle")
    s.text(mx + mw / 2, 410, "→ 移进 已处理/", "m", 12, anchor="middle")
    s.save("write-partition.svg", "学习类与总控类会话各自能写的文件，以及交接单通道")


# ====================================================================== review flow
def review_flow():
    W, H = 1200, 560
    s = SVG(W, H, "作业交前审查")
    s.rect(0.5, 0.5, W - 1, H - 1, "bg", rx=16)
    s.text(40, 52, "作业交前审查（/审查）", "t", 24, 700)
    s.text(40, 80, "按那份作业的 AI 政策分三档 · 默认 ≤ 10 个 agent · 扇出全用 sonnet，只有判官用主模型 · 每个 agent 先落盘再返回", "m", 14)

    # 三档
    tiers = [("allowed", "允许 AI", "给改法：OLD → NEW，他挑着改", "green"),
             ("guided", "不允许 AI 产出最终件", "只指出哪里错、为什么扣分，不给答案", "orange"),
             ("forbidden", "spec 禁止任何 AI", "不跑 AI 审查，只出一份自查清单", "red")]
    for i, (k, a, b, c) in enumerate(tiers):
        x = 40 + i * 380
        card(s, x, 102, 360, 76, c)
        s.chip(x + 20, 116, k, c, 13, mono=True, h=24)
        s.text(x + 20 + tw(k, 13, True) + 30, 133, a, "t", 14.5, 700, maxw=360 - tw(k, 13, True) - 60)
        s.text(x + 20, 162, b, "m", 13, maxw=330)

    # 主流程
    y = 214
    steps = [
        (40, 150, "① 定档位", "只能等于或严于分级表", "gray", None),
        (210, 150, "② 机械预检", "冻结 · 哈希 · 页数字数", "gray", None),
        (380, 130, "③ 问一次", "N 个 agent · 开始？", "gray", None),
        (530, 170, "④ Find", "每个镜头一个 finder", "blue", "sonnet × 4–5"),
        (720, 130, "⑤ Merge", "去重 · 核引文", "blue", "sonnet × 1"),
        (870, 150, "⑥ Verify", "facts → marker", "orange", "sonnet × 2"),
        (1040, 120, "⑦ Judge", "复现 · 分级", "purple", "主模型 × 1"),
    ]
    for (x, w, a, b, c, m) in steps:
        card(s, x, y, w, 92, c, accent=(c != "gray"))
        s.text(x + w / 2, y + 32, a, "t", 15, 700, "middle", maxw=w - 14)
        s.text(x + w / 2, y + 54, b, "m", 12.5, anchor="middle", maxw=w - 14)
        if m:
            s.text(x + w / 2, y + 76, m, "c-" + c, 12, 600, "middle", mono=True, maxw=w - 14)
    for x1, x2 in [(190, 210), (360, 380), (510, 530), (700, 720), (850, 870), (1020, 1040)]:
        s.line([(x1, y + 46), (x2 - 2, y + 46)])

    # forbidden 分支
    s.path(f"M115,{y + 92} L115,{y + 128} L300,{y + 128}", color="red")
    s.rect(302, y + 110, 330, 38, "s-red", rx=8)
    s.add(f'<rect x="302" y="{y + 110}" width="330" height="38" rx="8" fill="none" class="k-red" stroke-width="1.2"/>')
    s.text(318, y + 134, "forbidden → 作业审查.py checklist（零模型 · 零网络）", "c-red", 13, 600)

    # 报告 → 他改 → 下一轮
    ry = 400
    card(s, 40, ry, 340, 120, "purple")
    s.text(62, ry + 32, "报告（报告-rN.md → PDF）", "t", 15, 700)
    for j, t in enumerate(["必改 · 值得改 · 留着不改（理由必填）", "已驳回进 settled，下一轮不再提", "只给客观计数，不写「可以交了」"]):
        s.text(62, ry + 58 + j * 21, t, "m", 13)
    card(s, 400, ry, 360, 120, "green")
    s.text(422, ry + 32, "他决定改哪些", "t", 15, 700)
    for j, t in enumerate(["allowed：多选挑补丁，「我改 / 你自己改」", "guided：他自己改，我只重跑机械检查", "改完 prep --round N+1，只审差异"]):
        s.text(422, ry + 58 + j * 21, t, "m", 13)
    card(s, 780, ry, 380, 120, "gray", accent=False)
    s.text(802, ry + 32, "停下来的条件（任一）", "t", 15, 700)
    for j, t in enumerate(["没有新的必改 / 值得改 · 跑满 3 轮", "哈希没变 · 必改全是上一轮引入的回归", "离截止不到 6 小时 · 他说停"]):
        s.text(802, ry + 58 + j * 21, t, "m", 13)
    s.line([(1100, y + 92), (1100, 372), (210, 372), (210, ry - 2)], color="purple")
    s.line([(380, ry + 60), (398, ry + 60)])
    s.line([(760, ry + 60), (778, ry + 60)])
    s.save("review-flow.svg", "作业交前审查的三档政策和七步流程")



banner(); architecture(); lifecycle(); study_loop(); partition(); review_flow()
for w in WARN:
    print("⚠️", w)
print("done:", sorted(os.listdir(OUT)))
