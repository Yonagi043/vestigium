# 工具说明

[English](TOOLS.md) · [README](../README_ZH.md)

Vestigium（迹）是一个名叫 `vestigium` 的 MCP 服务，有 6 个工具。除 `region` 还会返回一张图片外，所有工具都只返回纯文本。除非另有说明，下面的例子都是样例文档（一份 10 页的 GAO 报告）上的真实输出，标了 `…` 的地方有删节。跨文档检索的例子来自一个有 59 份公开文档的文档库。

## 6 个工具

| 工具 | 参数（默认值） | 返回 |
|---|---|---|
| `list_documents` | `filter`（""）：名字的一部分，或文档类别 | 每份文档一行：名字、类别、页数、token 数、章节结构是否可靠 |
| `outline` | `doc`、`depth`（2）、`section`（无） | 章节树：地址、编号和标题、PDF 页码（括号里是印刷页码）、token 数、图的张数。`section="s12"` 展开某一节 |
| `search` | `doc`、`query`、`k`（5，最多 10；按块搜索至少 3）、`unit`（`block` 或 `page`）、`mode`（`bm25` 或 `literal`） | 按相关度排的命中：地址 · 页码 · 章节路径 \| 命中的句子 |
| `read` | `doc`、`addrs`（列表）、`max_tokens`（8000，也是上限） | 这些地址的原文，按页、按节排好 |
| `context` | `doc`、`addr`、`max_tokens`（1500，最多 4000）、`part`（无） | 这个地址在哪：章节路径、上级节开头、同级和下级小节、引用出去和引用进来、用到的术语定义、脚注 |
| `region` | `doc`、`target`、`scale`（1.5，0.5 到 3.0）、`preview`（false） | 表格、图、块、页面或一个框的 PNG 图片，附文字层。`preview=true` 只给版面和坐标 |

`doc` 可以写登记的全名，也可以只写名字里能唯一确定一份文档的一段，比如 `107790`、`800-53r5`。

### `outline`

```
gao-26-107790 · 10 pages (printed 1–10) · 6.0k tokens · structure reliable · …
s1 Why This Matters · p1 · 4
s2 Key Takeaways · p1 · 491
…
s6 What lessons have FEMA and disaster service providers learned about supporting this population? · p5-7 · 1.2k · 1 fig
…
s11 Endnotes · p9-10 · 1.2k
```

### `search`

普通搜索按关键词相关度（BM25）排序。节标题也会计入；每张图的图注和图内的文字合在一起，作为一段来搜：

```
search "lessons learned mothers infants formula" · blocks · top 5 · …
1. t12 · p1 · s2 Key Takeaways | FEMA officials and service providers told us about lessons learned, such as to evacuate children with their families …
…
4. t85 · p5 · s6 What lessons have FEMA and disaster ser… | …Service providers from one organization told us about lessons they learned about prioritizing ready-to-feed bottles over cans of powdered formula …
```

`mode="literal"` 按原文精确匹配，并数出全部命中。适合找数字、编号，以及判断“文档里到底有没有提到 X”：

```
4 matches for "ready-to-feed" in 4 blocks · literal · blocks
1. tab0 · p2 · 1× · s4 How have selected disaster service prov… | …Families may also need ready-to-feed formula, which is already in a bottle and…
2. t48 · p4 · 1× · …
```

### `read`

一次可以读多个地址。输出按页（`== p5 ==`）和节分组：

```
== p5 ==
## s6 · What lessons have FEMA and disaster ser… (cont.)
FEMA officials and service providers from the 12 local, state, and nonprofit organizations …
· Service providers from one organization told us about lessons they learned about prioritizing ready-to-feed bottles …
== p6 ==
…
next: p6 from t91
```

- 读到上限时，结尾会有一行 `next: …`。把这段原样作为下一个地址传回去，就能接着读。
- `continues: p151 (s17)` 表示这一节在没显示的页上还有内容。
- 读一个块时，会连同它所在页上同一节的其他内容一起给出，免得漏掉紧挨着的条件。读得少、而这一节还在往下延续时，会自动带上下一页。
- 你读的页面前后紧挨的一页，如果在你之前的搜索里排得靠前，也会附上，并标注 `[adjacent pages that match your searches]`。这一项依赖会话；除了加 `--new-protocol` 的 HTTP 模式，其余方式都有会话，见[各客户端的接法](CLIENTS_ZH.md)。

### `context`

回答之前用它核对一段内容的适用范围和条件：

```
context t85 · p5 · 1 blocks
path: s6 What lessons have FEMA and disaster service providers learned about supporting this population?
siblings (11):
  …
refs out (1):
  figure "fig. 4" → t92 p6 s6 (from t91)
```

输出被截断时，结尾是 `next: part=…`，用这个 `part` 再调一次即可。

### `region`

返回一张图片，附上图片范围内的文字层。一张图最多约 1,500 token，按大小而定。文字层不够用时再用 `region`：图表、示意图、扫描页，或者列错位的表格。`preview=true` 只列出这一页上的各个块和它们的框，不给图片：

```
p5 whole page · p5 · page 612×792 pt · box 0,792,612,0 (l,t,r,b; origin bottom-left)
t79 text 180,754,570,680 programs that can include counseling, case management, and
…
```

## 跨文档检索（实验性）

面向整个文档库的三项新功能。在小试证明有用之前默认都关着；要用的话，启动服务时打开。

- **在所有文档里搜**（`TGLW_SEARCH_LIBRARY=1`）：`search(doc="*", query=…)`。返回先列出最匹配的几份文档，再列命中，每条写成 `文档名:地址`：

  ```
  search "account management automated audit actions" · library: 59 documents · top 3 · blocks · …
  documents by best match: nist__NIST.SP.800-53r5 · nist__NIST.SP.800-171r3 · … (+41 more with matches)
  1. nist__NIST.SP.800-53r5:t603 · p48 (printed 21) · s30 AC-2 ACCOUNT MANAGEMENT > (4) … | …
  ```

- **一次读多份文档**（`TGLW_READ_DOC_ADDR=1`）：`read(doc="*", addrs=["nist__NIST.SP.800-53r5:AC-2(3)", "800-53B:p31"])`。文档名可以只写能唯一确定一份文档的一段。
- **列出全部命中**（`TGLW_SEARCH_LIST=1`）：`search(doc, query, mode="list")` 把所有精确命中只列成地址，按节分组并计数，适合“哪些节提到了 X”这类问题。太长时结尾是 `next: offset=N`，传 `offset=N` 接着列。

  ```
  1208 matches for "privacy" in 637 blocks, 297 sections · literal · every match, addresses only
  s4 Abstract: t45 p4 (4×)
  s5 Keywords: t47 p4 (4×)
  …
  ```

## 地址写法

| 写法 | 意思 | 例子 |
|---|---|---|
| `s12` | 节，地址见 `outline` | `s6` |
| `p45`、`p45-47` | PDF 页或一段页，从 1 开始数 | `p5-6` |
| `t123` | 一个文字块 | `t85` |
| `tab4` | 一整张表 | `tab0` |
| `fig5` | 一张图，连同图注 | `fig5` |
| `P36` | 印刷页码，即页面上印的数字 | `P36`、`Pxii` |
| `P3@Exhibit 10.21` | 几个附件各自从头编页码时，指明是哪个附件的那一页 | |
| 编号名称 | 文档自己编的附注、条目、章节、控制项 | `Note 7`、`Item 7A`、`§2.2`、`AC-2(3)` |
| `p45:l,t,r,b` | 页面上的一个框，单位是 PDF 点，原点在左下角（给 `region` 用） | `p5:180,131,577,57` |
| `fn: 69`、`fn69`、`fn. 69`、`footnote 69` | 正文以 69 开头的那条脚注。同一个号码有好几条脚注时，列出它们的地址让模型选 | NIST SP 800-53 里的 `fn: 12` |
| `文档名:地址` | 另一份文档里的地址，配合 `doc="*"` 用（实验性，`TGLW_READ_DOC_ADDR=1`） | `800-53B:p31` |

回答时请一律引用 **PDF 页码**（`p45`）。印刷页码和 PDF 页码不同时会一并显示，但它没那么可靠：很少有 PDF 自带页码标签，Vestigium 是从页面文字里推断出来的。

## 只开放部分文档

把 `TGL_ALLOWED_DOCS` 设成用逗号分隔的登记名。服务就只列出、只打开这几份文档，别的名字一律拒绝。一个文档库给多个项目用时很有用；我们测试时也靠它让每次运行只碰自己那道题的文档。

```bash
TGL_ALLOWED_DOCS=gao-26-107790,nist__NIST.SP.800-53r5 vestigium mcp
```

## 输出上限

- `read`：每次最多 8,000 token、40,000 字符。
- `context`：最多 4,000 token。
- `search`：最多 10 条；按块搜索时至少给 3 条。
- `outline`：最多 60 行；更多内容用 `section` 展开某一节。
- `region`：图片下面的文字层最多 2,000 token。

这些上限让每次返回都低于 Claude Code 把工具结果转存成文件的界线。有些软件对小得多的结果就会转存成文件。Vestigium 按软件连接时报的名字认出它们：对 VS Code，把每次返回切成每段不超过 8,000 字符的几段（内容不变）；对 GitHub Copilot CLI，每次返回控制在 18,000 字节以内，用 `next:` 续读。见下面的 `TGLW_MCP_LIMITS` 和[各客户端的接法](CLIENTS_ZH.md)。

## 开关

开关是环境变量，服务启动时读取。除非另有说明，`1` 表示打开，`0` 表示关闭。默认值写在代码里，`vestigium mcp` 不设任何开关也能运行。

### 设置

大多数开关的 `TGLW_` 前缀，和下面 `python -m tglw.corpus build` 里的导入包名 `tglw` 一样，来自这个项目早期的名字。

| 变量 | 默认 | 作用 |
|---|---|---|
| `VESTIGIUM_HOME` | `~/.vestigium` | 文档库的位置：PDF、解析结果、索引、质检报告 |
| `TGL_ALLOWED_DOCS` | 不设（全部文档） | 只开放这些文档（用逗号分隔的名字） |
| `TGLW_CACHE_MB` | 2048 | 内存里保留的已解析文档的内存预算，单位 MB。HTTP 服务预先加载文档也以它为限 |
| `TGLW_CORPUS_DB` | `$VESTIGIUM_HOME/data/corpus/corpus.sqlite` | 全库检索索引放在哪里 |
| `TGLW_MCP_LIMITS` | `auto` | 按软件设输出上限：`auto` 按软件报的名字来定；`off` 对所有软件都用 Claude Code 的上限；`vscode` 或 `copilot` 强制用那个软件的上限。见[输出上限](#输出上限) |

### 默认打开

把这些全部关掉、再设 `TGLW_MCP_LIMITS=off`，在我们测过的文档上，工具输出和实测版本逐字节相同（回放了 1,826 次录下的工具调用核对过）。

| 变量 | 作用 |
|---|---|
| `TGLW_VIEWS_FOOTNOTES` | `fn: 69` 这样的脚注地址 |
| `TGLW_ZH_ADDRESSES` | “第二十七条”“第三章”“附錄二”这样的中文条、章、附录编号可以当地址用 |
| `TGLW_VIS_FIG_HINT` | `read` 在每张图后面标上 `(image: region figN)` |
| `TGLW_VIS_CROP_BOX` | `region` 写出返回的图片在页面上的坐标框 |
| `TGLW_VIS_OCR_NOTE` | 来自 OCR 的文字（扫描页）标明可能有错 |
| `TGLW_VIS_DRAWINGS` | Docling 没切成图的矢量图，给出它在页面上的坐标 |
| `TGLW_VIS_OUTLINE_FIGS` | `outline` 写出每节有几张图 |
| `TGLW_VIS_LITERAL_FIGS_LAST` | 字面检索时，图里的命中排在正文命中之后 |
| `TGLW_ING_QC_NOTES` | `vestigium ingest` 的质检结论显示在 `list_documents`、`outline`、`read` 里 |
| `TGLW_VIEWS_HEADING_NEXT` † | 搜索命中只是一个短标题时，顺带给出它后面的那段 |
| `TGLW_SRC_TABLE_NOTES` † | 表格的行可能并在一起、或者一格里有几个数时，在表头提醒，并让模型去看页面原图 |
| `TGLW_SRC_LOOSE_TABLES` † | Docling 没认出来的表，在原来的位置显示重建后的表 |
| `TGLW_SRC_BOX_FIX` † | `region` 和 `preview` 对解析器放错了框的块，改用它在页面上的真实位置（多见于扫描页） |
| `TGLW_SRC_DUP_BLOCKS` † | 原文在同一页上看得到时，藏掉放错位置的重复行 |
| `TGLW_SRC_ROTATED_PAGES` † | 藏掉旋转页的文字层里的乱码块 |
| `TGLW_ZH_DISPLAY` † | 默认 `auto`：在中文文档里，去掉汉字之间多余的空格，摘录按中文句号切。`1` 对所有含汉字的文字都这样做，`0` 关闭 |
| `TGLW_SEARCH_CJK_AUTO` † | 中文文档用专为中文建的检索索引，中文查询才能搜到 |
| `TGLW_SEARCH_FOLD` † | 中文检索和字面检索把繁体、简体当成同一个字；显示的仍是原文 |
| `TGLW_VIS_ADAPTIVE_ZOOM` † | `region` 按图里最小的字号来定截图倍数（1.5 到 3 倍）。它还把所有截图长边的上限从 1,200 像素放宽到 1,568 像素（面积上限不变），所以模型自己指定倍数的截图也可能变大。回放录下的调用，图片 token 合计多了约 12% |

没有 † 的开关只是多给一些信息，或者让原来会报错的地址能读出内容。标 † 的开关会改变模型看到的内容，都是检查过以后才默认打开的。6 个文字方面的开关在 65 道开发题上检查：用 Haiku，这一版得分 97.2%，我们实测的版本 97.1%，花费是后者的 0.92，事先定的 5 条“不退步”规则全部通过。截图倍数在 15 道要看图的开发题上检查，比固定倍数没有变差。3 个中文开关只对中文文档起作用，在 15 道中文开发题上检查过，见[结果](RESULTS_ZH.md#中文实验性)。每题只跑了一次，Haiku 读图本身波动又大，所以两项检查我们都理解为“没有退步”，而不是“更好”，见[结果](RESULTS_ZH.md#这一版和实测版本)。

`TGLW_SRC_BOX_FIX`、`TGLW_SRC_DUP_BLOCKS`、`TGLW_SRC_ROTATED_PAGES` 这三个开关，修的是旧版 Docling 设置解析出来的问题。它们只在带有相应修正文件的文档库里起作用，而 `vestigium ingest` 不生成这些文件，所以在你自己的文档库里它们不起作用。用 `vestigium ingest` 入库的文档本来也基本用不上：把 6 份文档用现在的设置重新转换后，放错的框从 574 个降到 3 个，重复块从 424 个降到 0 个。

### 默认关闭（实验性）

这些会改变模型看到的说明或内容，在小试证明有用之前一律关闭。

| 变量 | 作用 |
|---|---|
| `TGLW_SEARCH_LIBRARY` | `search(doc="*")` 在所有文档里搜，见[跨文档检索](#跨文档检索实验性) |
| `TGLW_READ_DOC_ADDR` | `read`、`context`、`region` 接受 `文档名:地址` |
| `TGLW_SEARCH_LIST` | `search(mode="list", offset=N)` 把全部命中列成地址 |
| `TGLW_MCP_ANSWER_HINT` | 服务说明里提醒模型逐项作答，每项写上页码。在 21 道开发题上的小试里，它没有提高准确率 |
| `TGLW_SRC_RUNNING_HEADERS` | 隐藏被解析器误当成标题的页眉，不再让它们把一节切开。打开以后，要用同样的设置重建全库索引：`python -m tglw.corpus build` |
| `TGLW_VIS_TOKEN_NOTE` | 工具说明里写出一张图大约多少 token，而不是说图片很贵。在 15 道看图题上的小试里，它没有提高准确率 |
| `TGLW_VIS_FIG_LABELS` | 把图里很长的标签清单截短（可能丢信息） |
| `TGLW_CJK` | 所有文档（不只是中文文档）都用中文检索（旧开关） |

中文是实验性支持，见[结果](RESULTS_ZH.md#中文实验性)。

### `vestigium mcp --http` 的命令行参数

`--host`（默认 127.0.0.1）、`--port`（8765）、`--preload`（`all`）、`--new-protocol`（不加）。见[各客户端的接法](CLIENTS_ZH.md)。
