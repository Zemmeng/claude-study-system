# MySQL Workbench GRT 脚本 API —— 探明记录

> 🔒 **这份文件的意义：这些都是拿真实启动试出来的，一次 30–60 秒。
> 下次要写 Workbench 脚本，先读这里，不许重新探。**
> 探明日期：**2026-09-02**（给一份数据库作业改 ER 模型时）· Workbench 在 `/Applications/MySQLWorkbench.app`
> 文中提到的 `导出ERD.py` · `排版体检.py` 等脚本是原作者在自己作业目录里写的，**不随模板发布**；这里记的是思路和踩过的坑。

## 怎么跑一个脚本（✅ 已验证）

```bash
/Applications/MySQLWorkbench.app/Contents/MacOS/MySQLWorkbench \
  --run-script "/绝对/路径/脚本.py" > /tmp/wb.log 2>&1 &
sleep 55
pkill -f MySQLWorkbench
```

- 🔴 **`--model <file>` 不管用** —— 2026-09-02 实测：GUI 起来了，但 `grt.root.wb.doc` 一直是 `None`，
  模型根本没加载。**必须在脚本里自己调 `grt.modules.Workbench.openModel(路径)`**（返回 `0` = 成功）。
- 🔴 **`--run-script` 在 GUI 起来之前就执行**，所以脚本主体必须延迟。
  ✅ 正确写法是**轮询**，不是固定 `add_timeout(6.0, ...)`（6 秒不够）：

```python
_t=[0]; _o=[False]
def wait():
    _t[0]+=1
    doc=getattr(grt.root.wb,'doc',None)
    n=len(getattr(doc,'physicalModels',[])) if doc is not None else -1
    if n>0: go(); return False          # 返回 False = 停止重复
    if not _o[0] and _t[0]>=2:          # 头两拍等 GUI，然后开模型
        _o[0]=True
        grt.modules.Workbench.openModel(P)
    if _t[0]>40: return False
    return True                          # 返回 True = 再来一次
mforms.Utilities.add_timeout(2.0, wait)
```
  实测节奏：`try=1` doc 无 → `try=2` 调 openModel → `try=3` 模型就绪。**总共约 6 秒**，但要留够 45 s+ 给 GUI 冷启动。

- 🔴 **`timeout` 命令 macOS 上不存在**，用 `cmd & sleep N; pkill -f MySQLWorkbench`。
- **每一步都要往文件写日志**（`io.open(OUT,'w').write(...)` 每次覆盖全量），否则崩了什么都看不到。
  stdout 重定向到 `/tmp/wb.log` **基本永远是空的**，别指望它。

## 对象怎么拿

```python
pm   = grt.root.wb.doc.physicalModels[0]
cat  = pm.catalog
sch  = cat.schemata[0]                       # 你的 schema 名
T    = {t.name: t for t in sch.tables}
dia  = [d for d in pm.diagrams if d.name=='Full Model'][0]
F    = {f.table.name: f for f in dia.figures if hasattr(f,'table') and f.table}
grt.modules.Workbench.saveModel()            # 存盘
```

⚠️ **`dir()` 对 GRT 对象没用** —— 只返回 `deep_copy, reset_references, shallow_copy`。
属性名要么照 `document.mwb.xml` 里的 `key=` 抄，要么看下面这张表。
同理 `dir(grt.modules.WbModel)` **返回空**，GRT 模块不支持内省。

## 建对象（✅ 全部验证过能建、能存）

`grt.classes` 里可用的：`db_mysql_Table` `db_mysql_Column` `db_mysql_Index`
`db_mysql_IndexColumn` `db_mysql_ForeignKey` `workbench_physical_TableFigure`
`workbench_physical_Connection` `workbench_physical_Layer` `workbench_physical_Diagram`

**通用套路：先 `owner=`，再设属性，最后 `append` 到父容器的列表里。**

| 建什么 | 关键属性 |
|---|---|
| **Table** | `owner=sch` · `name` → `sch.tables.append(t)` |
| **Column** | `owner=t` · `name` · **`c.setParseType('INT', cat.simpleDatatypes)`** ✅ 可用 · `isNotNull=0/1` → `t.columns.append(c)` |
| **Index** | `owner=t` · `name` · `indexType='PRIMARY'/'INDEX'/'UNIQUE'` · `isPrimary` · `unique` → `t.indices.append(ix)`；PK 还要 `t.primaryKey=ix` |
| **IndexColumn** | `owner=ix` · `referencedColumn=<Column>` · `columnLength=0` · `descend=0` → `ix.columns.append(ic)` |
| **ForeignKey** | `owner=t` · `name` · `referencedTable` · `columns.append()` × n · `referencedColumns.append()` × n · `deleteRule/updateRule='NO ACTION'` · `index=<支持索引>` · `many` · `mandatory` · `referencedMandatory` → `t.foreignKeys.append(fk)` |

**FK 的三个基数属性**（决定 Crow's Foot 怎么画）：
- `many` — 子表端是不是「多」。**1:1 关系写 `many=0`**
- `mandatory` — 子表端参与是否强制（FK 列 NOT NULL → 1）
- `referencedMandatory` — 父表端参与是否强制
- 参照：一个 1:1 且两端都强制的 FK（如 `fk_Employee_Dept`）是 `many=0 mandatory=1 referencedMandatory=1`

`cat.simpleDatatypes` 里有：`TINYINT SMALLINT MEDIUMINT INT BIGINT FLOAT REAL DOUBLE
DECIMAL CHAR NCHAR VARCHAR NVARCHAR BINARY VARBINARY TINYTEXT TEXT MEDIUMTEXT
LONGTEXT TINYBLOB BLOB MEDIUMBLOB LONGBLOB JSON DATETIME DATE TIME YEAR TIMESTAMP
GEOMETRY POINT LINESTRING POLYGON GEOMETRYCOLLECTION MULTIPOINT MULTILINESTRING
MULTIPOLYGON BIT BOOLEAN ENUM SET`

## 🔴 建 TableFigure / Connection —— **GRT 里会挂，改走 XML**

2026-09-02 实测：结构改动（表 / 列 / 索引 / FK）全部建成功并写进日志，
**一执行到 `grt.classes.workbench_physical_TableFigure()` + `dia.figures.append(f)` 就整个进程无声死掉** ——
没有 Python 异常（`except` 抓不到）、`/tmp/wb.log` 空、模型没保存（md5 未变）。是 C++ 层崩的。

✅ **可行的分工**：
1. **GRT 脚本只做结构**（表 / 列 / 索引 / FK）+ `saveModel()`
2. **figure 和 connection 直接改 `document.mwb.xml`**（`.mwb` 就是 zip，`unzip` / `zip` 即可）

`.mwb` 里那两种节点长这样，照抄改 id 就行（id 是大写 GUID，自己生成唯一的）：

```xml
<value type="object" struct-name="workbench.physical.TableFigure" id="<GUID>" struct-checksum="0x3b14eccb">
  <value type="int" key="columnsExpanded">1</value>
  <value type="int" key="foreignKeysExpanded">0</value>
  <value type="int" key="indicesExpanded">0</value>
  <value type="int" key="summarizeDisplay">-1</value>
  <link type="object" struct-name="db.Table" key="table"><表的 id></link>
  <value type="int" key="triggersExpanded">0</value>
  <value type="string" key="color">#98BFDA</value>
  <value type="int" key="expanded">1</value>
  <value type="real" key="height">122</value>
  <link type="object" struct-name="model.Layer" key="layer"><layer id></link>
  <value type="real" key="left">92</value>
  <value type="int" key="locked">0</value>
  <value type="int" key="manualSizing">0</value>
  <value type="real" key="top">100</value>
  <value type="real" key="width">136</value>
  <link type="object" struct-name="model.Diagram" key="owner"><diagram id></link>
  <value type="int" key="visible">1</value>
  <value type="string" key="name">Dept</value>
</value>
```

```xml
<value type="object" struct-name="workbench.physical.Connection" id="<GUID>" struct-checksum="0x9baebc92">
  <value type="string" key="caption">fk_Employee_Dept</value>
  <value type="real" key="captionXOffs">0</value>   <!-- captionYOffs / endCaption* / extraCaption* / startCaption* 同样都要有 -->
  <value type="string" key="comment"></value>
  <link type="object" struct-name="db.ForeignKey" key="foreignKey"><FK 的 id></link>
  <value type="real" key="middleSegmentOffset">0</value>
  <value type="int" key="drawSplit">0</value>
  <link type="object" struct-name="model.Figure" key="endFigure"><父表 figure id></link>
  <link type="object" struct-name="model.Figure" key="startFigure"><子表 figure id></link>
  <link type="object" struct-name="model.Diagram" key="owner"><diagram id></link>
  <value type="int" key="visible">1</value>
  <value type="string" key="name"></value>
</value>
```

- `struct-checksum` 是**类型级**常量，同类型全都一样，照抄。
- figure 的 `width/height` 给个近似值即可，`manualSizing=0` 时 Workbench 打开会自己重算。
  参照：3 列的表是 `136 × 122`。

## 🔴 导出导的是「GUI 当前活动的 tab」，不是你手上的 diagram 对象（09-02 血泪）

`exportPNG/SVG/PDF` **不接受 diagram 参数** —— 它导的是 Workbench 窗口里当前打开的那张图。
后果：脚本里 `full = [d for d in pm.diagrams if d.name=='...'][0]` 拿得再对，导出来的也可能是另一张。

**症状**：导出的 SVG 只有 50 KB 左右（正常 780 KB），图上只有两三个表，
其中一个还显示 `10 more...` —— 那是**另一张图**渲染到一半的样子，不是"渲染时间不够"。多等 25 秒没用。

三条都要做：

| 做什么 | 怎么做 |
|---|---|
| ① **切到目标图** | `grt.modules.Workbench.activateDiagram(<diagram 对象>)`，返回 `0` = 成功 |
| ② **固定下次打开的图** | `pm.currentDiagram = full` 然后 `saveModel()` —— 只对**下一次**打开生效，本次导出不看它 |
| ③ **触发全量重绘** | canvas 是懒渲染的。`导出ERD.py` 每次重排全部 figure 的 `left/top`，顺带解决了这条 |

⚠️ `dir(grt.modules.Workbench)` **返回空**，但 `getattr` 是动态的 —— 函数名猜对了就能拿到。
已确认存在：`openModel` `saveModel` `closeModelFile` `activateDiagram` `exportPNG/SVG/PDF`。
不存在：`closeModel` `openDiagram` `switchDiagram` `showDiagram`。

## 🔴 全局选项的 type 必须写对，写错会让 canvas 构建直接崩（09-02）

选项在 `grt.root.wb.options.options`，落盘在
`~/Library/Application Support/MySQL/Workbench/wb_options.xml`。

**布尔类选项是 `type="int"`，字体类是 `type="string"`。**
在 GRT 里赋值 `o[KEY]='1'`（字符串）会把它存成 `type="string"` →
下次打开模型时 canvas 构建**中途静默中断**（只画两三个 figure），
`activateDiagram` 也会报 **`Type mismatch: expected type int, but got string`**。

→ **改错了就直接改 `wb_options.xml`**（改之前 Workbench 必须没在跑），把 `type="string"` 改回 `type="int"`。

### 关系名（connection caption）怎么显示出来

| 选项 | 类型 | 意思 |
|---|---|---|
| `workbench.physical.Connection:ShowCaptions` | **int** | `1` = 在连线上显示 caption |
| `workbench.physical.Connection:CenterCaptions` | **int** | `1` = 放线的中点（**实测很糟**，中点常落在别的表框上）· `0` = 靠起点端 |
| `workbench.physical.Connection:CaptionFont` | string | `Helvetica 9` 之类 |

📌 **caption 的默认值是 FK 名**（`fk_Employee_Dept1`），太长，约 1/3 会被相邻表框盖住。
`c.caption = u"Employee–Dept"` 这样改短（子表–父表）能明显减少遮挡；caption 是显示用的，**改它不影响 FK 名**。
📌 还要把表间距拉开（`导出ERD.py` 里 `GX,GY`），否则标签没地方放。
　实测 `78/62 → 104/84`，图从 386×297mm 变成 417×315mm，重叠从「一半」降到「约 10 处」。

⚠️ **这些是全局 preference，改了会影响他自己开 Workbench 看任何模型。**
　2026-09-02 改动前的备份留在 `~/Library/Application Support/MySQL/Workbench/wb_options.xml.bak-0902`。

## ⚠️ `pkill` 掉 Workbench 会留下工作目录残留

`~/Library/Application Support/MySQL/Workbench/<模型名>.mwbd/` —— 下次打开可能拿它当恢复源，
状态就对不上了。**pkill 之后顺手 `rm -rf` 掉那个目录。**

## 🔴 figure 的 left/top 是**相对它所在 layer** 的，不是绝对坐标（09-02）

`导出ERD.py` 会把每个 figure 分配给一个自建的分区 Layer（`F[n].layer = L`）。
**一旦分配成功，`figure.left/top` 就变成相对那个 layer 的坐标。**

从 XML 读几何数据时必须自己加回去：

```python
绝对 left = figure.left + layer.left      # layer 名字为空的是 diagram 根 layer，偏移 0
绝对 top  = figure.top  + layer.top
```

⚠️ 不加会静默错位（实测整整差了一个 layer 的 926pt），而且**所有 figure 差的量一样**，
看起来像"整体平移"，很容易误判成别的问题。**发现所有 figure 的偏差是同一个常数 → 就是这条。**

## 自动把连线标题推开（09-02 做过一遍，整套方法可复用）

Workbench 自己不避让，45 条 caption 里有 23 条压在表框上。**逐条调 `captionXOffs/captionYOffs` 是可行的**，
但要先解决"怎么知道哪条压住了"。SVG 里文字被转成 `<use>` 字形引用（`<text>` 数量为 0），读不出；
**PDF 可以**：

```bash
pdftotext -bbox 图.pdf 出来.html     # 每个词一行，带 xMin/yMin/xMax/yMax
```

流程（脚本都在 `/private/tmp/.../scratchpad`，做法记在这里，下次照抄）：

| 步 | 做什么 |
|---|---|
| 1 | 从 `.mwb` XML 读 33 个 figure 的绝对矩形（**记得加 layer 偏移**）和 45 条 connection 的 caption |
| 2 | **标定 canvas→PDF 的变换**：拿表名文字的坐标和 figure 的 left/top 做拟合。🔴 **必须用稳健拟合**（穷举表对 + 数内点），因为有的表名在图里出现不止一次，普通最小二乘会被带偏（实测残差 330pt，全错） |
| 3 | 把 figure 矩形映射到 PDF 坐标，检测哪些 caption 的 bbox 和它们相交 |
| 4 | 对每条冲突的 caption 网格搜索 `(dx,dy)`，取**不压任何表框、也不压其他 caption** 的最小位移 |
| 5 | 位移 ÷ scale 换算回 canvas 单位，写进 XML 的 `captionXOffs/captionYOffs` |
| 6 | 重导 → 重新检测 → 迭代（实测两轮：23 条 → 1 条 → 0 条） |

⚠️ **XML 里 caption 的 en dash 存成实体 `&#x2013;`**，按文本匹配 connection 前要先 `html.unescape`，
否则一条都匹配不上（第一次就栽在这儿，"写入 0 条"）。

⚠️ 判定净空建议留 **5pt**，只留 2pt 的话文字会紧贴框线 —— 表框画在 caption 之上，贴边看起来就像被裁掉。

## 🔬 从导出的图里量排版（09-04 建的整套，脚本在 `<课程>/作业/_导出/脚本/`，不随模板发布）

要判断「线穿没穿过表框」「标签压没压到线」，靠眼睛看会漏，必须量。四个脚本：

| 脚本 | 干什么 |
|---|---|
| `排版体检.py <tag>` | 四项几何检测：① 线穿过**无关**表框 ② 标签压**别的**线 ③ 标签压表框 ④ 标签互压 |
| `搜布局.py GX GY ZGX ZGY 次数 输出名` | 搜分区位置 × 区内表顺序 × 列数 × 空位通道 |
| `断线.py <tag>` | 把体检查出来穿表的线设成 `drawSplit` |
| `推标签.py <tag>` | 逐条算 `captionXOffs/YOffs`，把标签推到空位 |

### 🔴 四个必踩的坑（每一个都让我得出过错误结论）

**① 🔴🔴 表框坐标只能【直接从 SVG 里取】，两种"换算"都是错的**

试过两种换算，**都翻车了**：

| 错法 | 后果 |
|---|---|
| 拿**表名文字**拟合平移量 | 表名相对框左边有约 27 canvas 内边距（图标+padding）→ 表框整体**右移 ~15pt**，标签被框吃掉却报"没重叠" |
| 拿某个 `<path>` 的 **`transform` 平移量** | 布局里一旦出现**负坐标**（分区框 left=−82），Workbench 导出时会把画布整体平移，而**各元素的 transform 平移量并不一致** → 表框整体偏 **~47pt**，四项检测全错 |

🔴 **第二种错法坑了整整一晚**：连报好几版"四项全 0"，实际每版都有 5–9 处问题，
肉眼一看就发现标签被框盖住，数据却说没有。

✅ **唯一可靠的做法**：表框在 SVG 里就是「**白底 `fill="rgb(100%, 100%, 100%)"` + 50% 灰边
`stroke="rgb(50%, 50%, 50%)"`**」的圆角矩形 path，每张表画两次，去重后得到 33 个框，
再用**表名文字的位置**（落在框顶部标题栏那一带）把框和表名对应起来。
这样连线、标签、表框**全部在同一个 PDF 坐标系里**，不经过任何换算。

⚠️ 缩放比例（`0.566929`）从 transform 读是准的 —— 错的只是**平移量**。
需要把 PDF 位移换算回 canvas（写 `captionXOffs` 那类）时，只用比例、别用平移。

**② 一个 `<path>` 里可能有多个 `M`** —— `drawSplit` 断开的两段就写在同一个 path 里。
不按 `M` 切开的话，中间那段"跳跃"会被当成实线，**误报**一批穿越。

**③ 每条连线两端还各画了 crow's foot / 基数记号**（2 点、长度 <10pt 的小 path），
不按总长度筛掉，45 条线会数成 208 条。

**④ `pdftotext -bbox` 报的是【被表框裁剪后的可见 bbox】** ——
标签尾巴被框盖住时它报的宽度是短的。SVG 里文字是 `<use x= y=>` 逐字引用字形，可以拿来补全。
　（不过实测两者差不多，真正的元凶是坑 ①。）

### Workbench 的走线规律（45 条线标定出来的）

- 全部是**正交折线**：`HVH` 18 · `VHV` 9 · L 形 12 · 直连 5
- 端点贴在表框**左右边外 ~26 canvas**（crow's foot 占位）或**上下边外 ~5**，沿边位置大致在中点
- 表框几何：**height = 50 + 20 × 列数**（canvas），行高 20、表头 25、底栏 25，**残差 0**
- 🔴 **它撞上谁就穿过谁，不会绕障碍**。所以"线不穿表"只能靠挪表位置，或者靠 `drawSplit`

### 🔴 figure 的 left/top 是**相对它所在 layer** 的

`导出ERD.py` 会把 figure 分配给分区 Layer，之后 `figure.left/top` 就变成相对那个 layer 的。
从 XML 读几何时必须 `绝对 = figure.left + layer.left`（layer 名字为空的是根 layer，偏移 0）。
⚠️ 症状是**所有 figure 差同一个常数**，看着像整体平移。

### `drawSplit`：让线不穿表的兜底手段

`connection.drawSplit=1` 会把线断开，只在两端各留一小截。
09-01 曾经否掉它（"断开那头看不出连到哪"）——**但那是在没有关系名标签的前提下。
现在每条线都标了名，断开处就有标签，可读性没问题**，09-04 用它把 11 条跨区长线断掉，穿越归零。
⚠️ `导出ERD.py` 里原来有一行 `for c in full.connections: c.drawSplit=0`，会把设定清零，已删。

### `middleSegmentOffset`：试过，不可控

想用它把折线中段推进通道里。实测**只改 2 条，Workbench 连锁重排了 10 条**，
而且位移语义不是简单平移。**放弃了，别再试。**

### 关系名标签（connection caption）

- 默认 caption 是 FK 名（`fk_Employee_Dept1`），太长，一大半会被相邻表框盖住
- 改成「子表–父表」（`c.caption = u"Employee–Dept"`）能大幅减少遮挡；**caption 只是显示用的，改它不动 FK 名**
- 🔴 XML 里 caption 的 en dash 存成实体 `&#x2013;`，按文本匹配 connection 前要先 `html.unescape`
- 🔴 写 `captionXOffs/YOffs` 时要**在原值上累加**，且**没算到的标签不许清零**
  （犯过：只写入本轮算出的几条、其余全设 0，把上一轮的成果全抹了）

## 🔴 caption 被锁在自己那条线附近，推不开

写进 `captionXOffs/captionYOffs` 的偏移**不会照单全收**。实测：

```
写 (20,  -2)    → 实际动 (+11, -2) pt   ✅ 生效
写 (39.9,-28.5) → 实际动 (+13, -1) pt   ❌ 大部分被吃掉
写 (-52.9, 14)  → 实际动 (-1,  +9) pt   ❌ X 方向完全没动
```

Workbench 把 caption 约束在它自己那条连线附近，只能**沿线小幅滑动**。
→ **「标签压表框」这类问题，推标签是推不开的**，只能反过来**挪被压住的那张表**
（`让表躲标签.py`）。

## 🔴 挪表的位移必须封顶，否则分区框会互相压

分区框（Layer）是按**框内表的包围盒**算的。挪表位移放到 ±300pt 时，表会跑出自己的分区，
分区框跟着撑大 → **几个分区框互相重叠**，marker 一眼就看出乱。
实测安全值：**单次 ±72pt，累计 ±190 canvas**。但收得这么紧又常常修不动 ——
真要修干净得回到**布局搜索**阶段，把「分区框不重叠」直接写进目标函数。

## 导出 ER 图（09-01 那次探明的，仍然有效）

- `grt.modules.Workbench.exportPNG / exportSVG / exportPDF(路径)` —— 导的是**当前选中的 diagram**
- `drawSplit=0`：断开显示的连线只留一个 cardinality 记号，看不出连到哪 → **全部关掉**
- 布局脚本和裁图脚本：`导出ERD.py` · `裁图.sh` · 布局数据 `ERD布局.json`（都在自己的作业目录里）
  ⚠️ 那两个脚本里的 `S=` 曾经写死在某次会话的 `/private/tmp/...` 下，**会随重启消失**，用前先确认路径

## 🔴 更正（2026-09-05）：caption 偏移【不会】被夹住，Workbench 精确照做

09-04 记过「Workbench 把 caption 锁在它自己那条线附近，写 (-52.9,14) 实际只动 (-1,+9)」——
**那是当时检测器算错表框位置导致的误判**。09-05 用修正后的检测器实测 24 条：
请求 `(-49.9,-13.6)pt` → 实际 `(-50.4,-13.0)pt`，**24/24 全部精确命中**（误差 <1pt）。
→ `captionXOffs/YOffs` 可靠，**GUI 里手拖也可靠**。

## 🔴 两个让「标签检测」全错的坑（2026-09-05）

**① PDF 里每个词被画两遍**（fill + stroke）：`is is recorded recorded in in`。
不去重的话按词序列匹配多词标签**永远匹配不上**。先按 (x,y,文本) 近似去重。

**② 按【标签文字】匹配连线会串**：动词标签大量重名（`has`×5 / `hosts`×4），
`推标签.py` 原来按 caption 文字找连线，会把给某一条算出的位移写给所有同名线（实测让结果变差）。
→ 改成**按几何**匹配：caption bbox 中心离哪条同名折线最近就是哪条（`推标签2.py`）。

**③ 短标签是长标签的前缀**（`is stored` ⊂ `is stored in`）→ 匹配必须**长的优先并占位**。

修好的检测器：`排版体检2.py`（45/45 全测到）。


## 🔴 2026-09-05 22:1x：Workbench 存盘后 figure 全部挂到【根 layer】、坐标变绝对

GRT 改结构 + `saveModel()` 之后再读 XML：原来挂在分区 layer（相对坐标）的 figure，**全部被 Workbench 改挂到根 layer（`718D4BEB…`），`left/top` 变成绝对 canvas 坐标**。
后果：手插进 XML 的新 figure 若挂在分区 layer（相对坐标），`挪一张表.py` 会把那个分区框**缩成只包住它**（它按 layer 链接找表，只找到这一个）。
✅ 做法：新 figure 一律挂根 layer、写绝对坐标；分区框矩形直接给值 —— `_导出/脚本/摆表.py <in> <out> "表=left,top" "layer:<id>=l,t,w,h"`。

## ✅ 同日验证过的三件

- `c.setParseType('CHAR(3)', cat.simpleDatatypes)` / `'CHAR(10)'` / `'INT'` 都好使，`formattedType` 立刻变
- GRT 里 `fk.mandatory=0` 直接改、存盘后 XML 里就是 0（不必走 XML）
- 建新表 + PK 索引 + 两条 FK（含复合 FK，列顺序要按父表 PK 顺序）全程 GRT，`saveModel` 后 forward-engineer 的 SQL 在 MySQL 8.0.46 实建零报错
- 建库测试不用他的密码：`mysqld --no-defaults --initialize-insecure --datadir=<scratch>` 起一个临时实例（`--port=3399 --socket=/tmp/claude-mysql-3399.sock`，**socket 路径 >103 字节会起不来**），灌 SQL，`mysqladmin shutdown`
- 跑脚本的包装：`_导出/脚本/跑wb.sh <脚本> <日志> [秒]` —— 等日志里出现 `DONE` 再 pkill，顺手清 `.mwbd`


## 🔴 2026-09-06 00:4x–01:0x：在导出的 SVG 上「手改走线」的一整套（`_导出/脚本/改走线.py` · `核绕线.py` · `字形解码.py` · `svg转pdf.sh`）

**为什么**：Workbench 的走线撞上谁就穿谁；改导出的 SVG 路径可以做到零穿表，且每一步都能机械核对。

- **连线 = 黑色描边、开放的 `<path>`**（`stroke-dasharray` = 虚线），坐标在 `transform="matrix(0.567,0,0,0.567,tx,ty)"` 里；重写 `d` 时用 `local=(abs−t)/s`。
  端点符号（crow's foot / 圈 / 杠）是另外的小 path（<12pt）—— **首尾端点和首末段方向不动，符号就自动对**。
- **47 条连线 path 在文档里连续出现，顺序 = XML `connections` 列表顺序**（含那条 <20pt 的短连线）。
- **caption 文字 = `<g fill="rgb(0%,0%,0%)">` 里的 `<use xlink:href="#glyph-3-N">`**（每条 5 组：4 组白色重影 + 1 组黑），
  🔴 **caption 用的字体是 `glyph-3-*`，字段文字是 `glyph-0-*`，分区标题 `glyph-1-*`**；空格也是一个字形。
  🔴 **caption 组在文档里的顺序【不是】XML 顺序**，不能按序对应。
  ✅ 解码办法：47 条 caption 文字已知（XML 里有）→ 「同长度候选 + glyph→字符 映射全局一致」做回溯求解（`字形解码.py`），45/47 解出（另外 2 条落在表框内被过滤）。
- **路由**：3pt 网格正交 A*，障碍 = 表框外扩 7pt + 现有标签外扩 3pt，转弯罚 8，贴别的线（±2 格膨胀）罚 10；起终点各留 14pt 原方向短桩。10 条穿表线全部改成零穿表，端点/方向/实虚线/符号/表框逐 path 核过不变（`核绕线.py`）。
- **标签跟线**：被改的线，原来贴在被挪走的段上的标签，搬到新线最长横段上方 5pt（避表框/别的标签/别的线），没横段就竖段居中。归属按解码文字 + 最近线。
- **Chrome 打印 SVG→PDF**：`svg转pdf.sh`（页幅取 viewBox）。

## ⚠️ caption 偏移量（captionXOffs/YOffs）不总是「写多少动多少」（2026-09-06 实测）

同一次导出里：标签 A 写 +45pt 动 +43 ✓；标签 B 写 +27 只动 +9；标签 C 写 +10 却跳到别的线段（+33,+49）。
走线本身没变（逐条比过）。推测 Workbench 会按线段重新选锚点。**改偏移一律「写 → 导出 → 量 → 再改」，别指望一次到位。**

## ⚠️ 改了列类型之后 Workbench 会按新文字自动缩窄表框、走线全变（2026-09-05 22:3x）

`manualSizing=0` 的 figure 每次打开都按文字重算宽度。把 5 个 VARCHAR(45) 改成 INT 之后十几张表变窄，
连线端点全部漂移、走线大变、之前调好的标签偏移全部失效。**解法：所有 figure `manualSizing=1`（`锁表框.py`），宽高照 XML 里存的旧值。**

## 🔧 2026-09-06 02:1x–02:4x：SVG 层面的三件新工具 + Chrome 打印失效（`<课程>/作业/_导出/脚本/`）

- **`线线重合体检.py <svg> [--tol pt] [--minov pt] [--region x0,y0,x1,y1]`**：两条连线平行段贴在一起（距离<tol、重合>minov）逐处报坐标（viewBox 相对 pt + 150dpi 像素），`--region` 列出经过某矩形的所有线段 —— 他截图说「这里线重合了」时先用它定位。
- **`手设走线.py <in> <out> "表A-表B: x,y x,y ..."`**：手工给中间拐点（`改走线.py` 的 A* 会为了躲占用格绕到图边缘，如某条线绕到 x=18/y=30）。⚠️ **最后一个拐点要写成和末端点同行/同列的那个点**（脚本把它对齐到端点，不会另加一点）。自动核首末段方向、正交、穿表、贴近。
- **`标签贴线.py <in> [<out>] [--set "文字=cx,cy;文字@旧cx,旧cy=cx,cy"]`**：逐个 caption 核「在不在自己线上」并按他 10 条规则重放不合格的；字形外框从 `<g id="glyph-3-N">` 的 path 坐标实测（caption 字高只有 ≈3.8pt，`改走线.py` 里假设的 7pt 偏大）。放不下的用 `--set` 手放。
- 🔴 **`svg转pdf.sh`（Chrome headless）02:1x 起打出来是 Letter 612×792**（同一脚本 01:20 还正常），原因没查。改用 **`rsvg-convert -d 72 -p 72 -f pdf -o out.pdf in.svg`**：`-d/-p 72` 让 1 用户单位 = 1pt，页幅 = viewBox（1182.23×981.88）；不加就按 96dpi 缩成 886×736。PNG 同理 `-d 150 -p 150 -f png`。字形全是 path，无字体问题。
