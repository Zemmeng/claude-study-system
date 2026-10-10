#!/usr/bin/env python3
"""
把项目里的 .md 笔记渲染成和 dashboard 同一质感的 PDF。

用法：
    python3 shared/工具/md2pdf.py <输入.md> [输出.pdf]

依赖 xelatex（TeX Live / MacTeX）。脚本自己找：先 PATH，再查常见安装位置
（~/texlive/*/bin/* · /Library/TeX/texbin · /usr/local/texlive/*/bin/* · /opt/homebrew/bin · /usr/bin …），
也可以用环境变量 XELATEX 直接指定。找不到会给安装提示。

字体：macOS 用 PingFang SC / Helvetica Neue / Menlo；Linux 用 Noto Sans CJK SC / DejaVu；
Windows 用 Microsoft YaHei / Arial / Consolas。不合适就用环境变量覆盖：
    MD2PDF_CJK_FONT · MD2PDF_MAIN_FONT · MD2PDF_MONO_FONT

为什么有这个（2026-08-11）：
    他明确要求「笔记/总结/错题本默认出 PDF，不要只给 md」。
    但 .md 是我上课时随手追加的源文件，改动频繁 —— 所以
    **.md 当源，PDF 当交付物**，用这个脚本一条命令重新生成。
    不依赖 pandoc，所以自己写。

支持的 markdown 子集（够用即可，不求全）：
    # ## ### 标题 · **粗体** · `行内代码` · ```代码块``` ·
    表格 · > 引用 · - 列表 · --- 分隔线
"""
import glob
import os
import platform
import re
import shutil
import subprocess
import sys


def find_xelatex():
    """先 PATH，再常见安装位置。返回可执行文件路径，找不到返回 None。"""
    env = os.environ.get('XELATEX')
    if env and os.path.exists(env):
        return env
    exe = 'xelatex.exe' if os.name == 'nt' else 'xelatex'
    hit = shutil.which('xelatex')
    if hit:
        return hit
    home = os.path.expanduser('~')
    pats = [
        os.path.join(home, 'texlive', '*', 'bin', '*', exe),          # 用户目录安装（install-tl 选了非系统路径）
        os.path.join(home, '.local', 'texlive', '*', 'bin', '*', exe),
        '/Library/TeX/texbin/' + exe,                                   # MacTeX
        '/usr/local/texlive/*/bin/*/' + exe,
        '/opt/homebrew/bin/' + exe, '/usr/local/bin/' + exe, '/usr/bin/' + exe,
        'C:\\texlive\\*\\bin\\*\\' + exe,                            # Windows TeX Live
    ]
    for pat in pats:
        found = sorted(glob.glob(pat), reverse=True)                   # 新版本号排前面
        if found:
            return found[0]
    return None


def pick_fonts():
    """(CJK 字体, 西文字体, 等宽字体)。环境变量优先，其次按操作系统给默认值。"""
    sysname = platform.system()
    if sysname == 'Darwin':
        d = ('PingFang SC', 'Helvetica Neue', 'Menlo')
    elif sysname == 'Windows':
        d = ('Microsoft YaHei', 'Arial', 'Consolas')
    else:
        d = ('Noto Sans CJK SC', 'DejaVu Sans', 'DejaVu Sans Mono')
    return (os.environ.get('MD2PDF_CJK_FONT', d[0]),
            os.environ.get('MD2PDF_MAIN_FONT', d[1]),
            os.environ.get('MD2PDF_MONO_FONT', d[2]))


PREAMBLE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[margin=1.7cm,top=2.0cm,bottom=1.7cm]{geometry}
\usepackage{xeCJK}
\usepackage{fancyhdr}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{array}
\usepackage{longtable}
\usepackage{ragged2e}
\usepackage{pifont}
\usepackage{fancyvrb}
\usepackage{fvextra}   % 🔒 2026-08-26 加：长命令行会冲出页面，fvextra 提供 breaklines
\usepackage[hidelinks]{hyperref}

% 字体：和 dashboard 一套（__CJK__ / __MAIN__ / __MONO__ 由 pick_fonts() 按系统填）。
% 缺 \setmonofont 会让 \texttt 掉进 Latin Modern Mono，
% 那个字体比 Helvetica 大一圈，夹在中文里一跳一跳（08-11 踩过）
\setCJKmainfont{__CJK__}
\setCJKsansfont{__CJK__}
\setCJKmonofont{__CJK__}
\setmainfont{__MAIN__}
\setmonofont{__MONO__}[Scale=MatchLowercase]

\definecolor{cool}{HTML}{0B4C8C}
\definecolor{hot}{HTML}{C0281C}
\definecolor{mute}{HTML}{6B6B6B}
\definecolor{band}{HTML}{F0F0F0}
\definecolor{codebg}{HTML}{F7F7F7}
\definecolor{ok}{HTML}{1A7F37}
\definecolor{warm}{HTML}{B54708}

\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\sffamily\color{mute} __HEADER__}
\fancyhead[R]{\small\sffamily\color{mute} \thepage}
\renewcommand{\headrulewidth}{0.4pt}

\setlength{\parindent}{0pt}
\setlength{\parskip}{4pt}
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.25}
\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}

\DefineVerbatimEnvironment{code}{Verbatim}{fontsize=\small,xleftmargin=8pt,frame=leftline,framerule=1.2pt,rulecolor=\color{cool!35},breaklines=true,breakanywhere=true,breakindent=2em,breaksymbolleft=\textcolor{cool}{\small$\hookrightarrow$},breaksymbolright={}}

\begin{document}
"""

SPECIALS = [('\\', r'\textbackslash{}'), ('{', r'\{'), ('}', r'\}'), ('&', r'\&'),
            ('%', r'\%'), ('$', r'\$'), ('#', r'\#'), ('_', r'\_'),
            ('~', r'\textasciitilde{}'), ('^', r'\textasciicircum{}')]

# 🔒 PingFang / Helvetica / Menlo 这类字体都没有 emoji 和数学箭头的字形，
#    直接输出会渲成豆腐块 □（2026-08-11 在 dashboard 和这里各踩一次）。
#    有语义的换成 pifont 记号，纯装饰的直接扔掉。
SYMBOLS = {
    '🔴': r'{\color{hot}\ding{115}}', '🔒': r'{\color{hot}\ding{115}}',
    '⚠️': r'{\color{warm}\ding{115}}', '⚠': r'{\color{warm}\ding{115}}',
    '🔑': r'{\color{cool}\ding{228}}', '💡': r'{\color{cool}\ding{228}}',
    '✅': r'{\color{ok}\ding{51}}', '✓': r'{\color{ok}\ding{51}}',
    '❌': r'{\color{hot}\ding{55}}', '✗': r'{\color{hot}\ding{55}}',
    '⬜': r'{\color{mute}\ding{113}}', '⚪': r'{\color{mute}\ding{109}}',
    '→': r'$\rightarrow$', '←': r'$\leftarrow$', '↔': r'$\leftrightarrow$',
    '⇒': r'$\Rightarrow$', '⟹': r'$\Longrightarrow$',
    '≥': r'$\geq$', '≤': r'$\leq$', '≠': r'$\neq$', '≈': r'$\approx$',
    '∈': r'$\in$', '∉': r'$\notin$', '⊆': r'$\subseteq$',
    '∩': r'$\cap$', '∪': r'$\cup$', '∞': r'$\infty$', '≺': r'$\prec$',
    '×': r'$\times$', '÷': r'$\div$', '±': r'$\pm$', '√': r'$\sqrt{\ }$',
}
# 其余 emoji 一律删掉（含变体选择符和肤色修饰符）
EMOJI = re.compile('[\U0001F000-\U0001FAFF←-⇿⌀-➿'
                   '⬀-⯿️‍⁠-⁯]')


_ESC = dict(SPECIALS)


def esc(t):
    """🔒 必须一次性替换。顺序 replace 会让 \\ 先变成 \\textbackslash{} ，
    其中的 { } 又被后面几轮转义成 \\{ \\} ，印出来是 \\{} （2026-09-19 交接单报的 bug）。"""
    return re.sub(r'[\\{}&%$#_~^]', lambda m: _ESC[m.group(0)], t)


def symbols(t):
    """在 LaTeX 转义之后调用 —— 这些替换结果里含反斜杠，不能再被转义。"""
    for a, b in SYMBOLS.items():
        t = t.replace(a, b)
    return EMOJI.sub('', t)


def inline(t):
    """行内 markdown → LaTeX。先抠出代码片段，避免里面的内容被当 markdown 处理。"""
    spans = []

    def stash(m):
        spans.append(m.group(1))
        return '\x00%d\x00' % (len(spans) - 1)

    t = re.sub(r'`([^`]+)`', stash, t)
    t = esc(t)
    t = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', t)
    t = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'\\textit{\1}', t)
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)          # 链接只留文字
    t = symbols(t)
    for i, c in enumerate(spans):
        t = t.replace('\x00%d\x00' % i, r'\texttt{%s}' % symbols(esc(c)))
    return t


def emit_table(rows):
    """rows 是 [[cell,...],...]，第一行当表头，第二行是 |---| 分隔线（已剔除）。"""
    ncol = max(len(r) for r in rows)
    total = 17.0
    width = total / ncol
    spec = ''.join('L{%.2fcm}' % width for _ in range(ncol))
    out = [r'\begin{longtable}{@{}%s@{}}' % spec, r'\rowcolor{band}']
    for i, r in enumerate(rows):
        r = r + [''] * (ncol - len(r))
        cells = [inline(c) for c in r]
        if i == 0:
            cells = [r'\textbf{%s}' % c if not c.startswith(r'\textbf') else c for c in cells]
        out.append(' & '.join(cells) + r' \\')
        if i == 0:
            out.append(r'\endhead')
    out.append(r'\end{longtable}')
    return out


def convert(md, header):
    lines = md.split('\n')
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]

        m_fence = re.match(r'^(`{3,})', ln)                       # 代码块
        if m_fence:
            fence = m_fence.group(1)          # 🔒 按开栏的反引号个数配对，
            i += 1                            #    否则 ```` 里嵌的 ``` 会提前收栏
            buf = []
            while i < len(lines) and not re.match(r'^%s`*\s*$' % fence, lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1
            out += [r'\begin{code}'] + [EMOJI.sub('', b) for b in buf] + [r'\end{code}']
            continue

        if ln.startswith('|') and i + 1 < len(lines) and re.match(r'^\|[\s:|-]+\|?\s*$', lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                if not re.match(r'^\|[\s:|-]+\|?\s*$', lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            out += emit_table(rows)
            continue

        m = re.match(r'^(#{1,4})\s+(.*)', ln)
        if m:
            lvl, txt = len(m.group(1)), inline(m.group(2))
            cmd = {1: r'\section*', 2: r'\subsection*', 3: r'\subsubsection*'}.get(lvl, r'\paragraph*')
            out.append(r'{\color{cool}%s{%s}}' % (cmd, txt))
            i += 1
            continue

        if re.match(r'^---+\s*$', ln):
            out.append(r'\vspace{2pt}\hrule\vspace{6pt}')
            i += 1
            continue

        if ln.startswith('> '):
            buf = []
            while i < len(lines) and lines[i].startswith('>'):
                buf.append(inline(lines[i].lstrip('>').strip()))
                i += 1
            out += [r'\begin{quote}\color{mute}'] + [' '.join(buf)] + [r'\end{quote}']
            continue

        if re.match(r'^\s*[-*]\s+', ln):
            buf = []
            while i < len(lines) and re.match(r'^\s*[-*]\s+', lines[i]):
                buf.append(r'\item ' + inline(re.sub(r'^\s*[-*]\s+', '', lines[i])))
                i += 1
            out += [r'\begin{itemize}\setlength\itemsep{1pt}'] + buf + [r'\end{itemize}']
            continue

        out.append(inline(ln) if ln.strip() else '')
        i += 1

    cjk, main_font, mono = pick_fonts()
    pre = (PREAMBLE.replace('__HEADER__', esc(header))
           .replace('__CJK__', cjk).replace('__MAIN__', main_font).replace('__MONO__', mono))
    return pre + '\n'.join(out) + '\n\\end{document}\n'


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + '.pdf'
    if not os.path.isfile(src):
        sys.exit('❌ 找不到输入文件：' + src)
    xelatex = find_xelatex()
    if not xelatex:
        sys.exit('❌ 找不到 xelatex。先装 TeX Live / MacTeX，例如：\n'
                 '   macOS:  brew install --cask mactex-no-gui   （装完重开终端）\n'
                 '   Debian/Ubuntu:  sudo apt install texlive-xetex texlive-lang-chinese texlive-latex-extra\n'
                 '   装在了非常规位置？  export XELATEX=/path/to/xelatex')
    header = os.path.basename(os.path.splitext(src)[0])
    tex = os.path.splitext(dst)[0] + '.tex'
    with open(src, encoding='utf-8') as f:
        md = f.read()
    with open(tex, 'w', encoding='utf-8') as f:
        f.write(convert(md, header))
    for _ in range(2):
        r = subprocess.run([xelatex, '-interaction=nonstopmode', '-halt-on-error',
                            os.path.basename(tex)],
                           cwd=os.path.dirname(os.path.abspath(tex)) or '.',
                           capture_output=True, text=True)
    if not os.path.exists(dst):
        print(r.stdout[-2500:])
        sys.exit('❌ 编译失败：' + dst)
    for ext in ('.aux', '.log', '.out', '.tex'):
        f = os.path.splitext(dst)[0] + ext
        if os.path.exists(f):
            os.remove(f)
    print('✅ ' + dst)


if __name__ == '__main__':
    main()
