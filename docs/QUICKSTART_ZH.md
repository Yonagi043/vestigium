# 快速上手

[English](QUICKSTART.md) · [README](../README_ZH.md)

从零开始，到得到第一个带页码的答案：安装、处理 PDF、接入 Claude Code、提问。

## 1. 需要什么

- Python 3.12 或更新的版本。
- [Claude Code](https://code.claude.com/docs)。我们只在它里面测过；别的软件见[各客户端的接法](CLIENTS_ZH.md)。
- 约 0.6 GB 磁盘空间，放 Docling 的模型；第一次处理 PDF 时自动下载。

## 2. 安装

```bash
python3 -m venv ~/.venvs/vestigium
source ~/.venvs/vestigium/bin/activate
pip install "vestigium[ingest] @ git+https://github.com/Yonagi043/vestigium"
```

Vestigium 直接从 GitHub 仓库安装，没有发布到 PyPI。`[ingest]` 会装上 Docling，处理你自己的 PDF 要用它。不装也行，只是只能使用已经处理好的文档，比如下面的样例。

可选：现在就把 Docling 的模型下载好（约 0.6 GB，放在 `~/.cache/docling/models`），免得第一次转换时再下：

```bash
docling-tools models download layout tableformer rapidocr
```

## 3. 先试样例

Vestigium（迹）自带一份处理好的样例：美国政府问责局（GAO）的 10 页报告 *Disaster Response: Lessons Learned in Supporting Mothers and Young Children*（GAO-26-107790，美国政府作品）。

```bash
vestigium ingest --sample        # 把样例加进你的文档库；不需要 Docling，几秒钟
```

它在文档库里的名字是 `gao-26-107790`。可以直接跳到[第 5 步](#5-接入-claude-code)。

## 4. 加入你自己的 PDF

```bash
vestigium ingest path/to/report.pdf
```

这条命令会用 Docling 解析 PDF，建好 Vestigium 的地图，并登记这份文档。文档名取文件名去掉 `.pdf`，特殊字符换成 `-`（`report.pdf` 就叫 `report`）；想换名字用 `--name`。一次可以处理多个 PDF。文字版的 PDF 每页约一秒或更快；扫描页每页要几秒（见[常见问题](FAQ_ZH.md#需要-gpu-吗)）。

所有东西都放进你的文档库，默认是 `~/.vestigium`：`docs/` 放 PDF 的副本，`cache/` 放 Docling 的解析结果，`data/` 放登记表、Vestigium 的地图、索引和质检报告。想放在别处，就在运行任何 `vestigium` 命令之前设好 `VESTIGIUM_HOME`。

**看一眼质检报告。** 每份文档都有一份：

```bash
vestigium ingest qc report
```

它会给出总体评价（`ok`、`check` 或 `poor`）；PDF 文字层的内容提取了多少；表格、图、标题各有几个；哪些页走了 OCR、OCR 的置信度多高；章节结构和印刷页码可不可靠；还有可疑页的清单和原因（没有文字、缺字、OCR 置信度低、文字层无法解码、页面旋转）。

其他命令：

| 命令 | 作用 |
|---|---|
| `vestigium ingest list` | 列出文档库里的文档 |
| `vestigium ingest remove 名字` | 删除一份文档 |
| `vestigium ingest report.pdf --force` | 重新转换 |
| `--device cpu\|cuda\|auto` | Docling 在哪里跑 |
| `--ocr rapidocr\|easyocr\|none` | 扫描页用哪个 OCR 引擎 |

## 5. 接入 Claude Code

```bash
claude mcp add --scope user --transport stdio vestigium -- vestigium mcp
claude mcp list            # 应当显示：vestigium ... ✔ Connected
```

- `--scope user` 让你的所有项目都能用 Vestigium。换成 `--scope project`，就只写进当前项目的 `.mcp.json`。
- 文档库不在 `~/.vestigium` 时，要把位置传进去：`claude mcp add --scope user --env VESTIGIUM_HOME=/path/to/library --transport stdio vestigium -- vestigium mcp`。
- 如果 Claude Code 找不到 `vestigium`，就写完整路径，比如 `-- ~/.venvs/vestigium/bin/vestigium mcp`。

在 Claude Code 的会话里，用 `/mcp` 查看服务是否在运行。

## 6. 提问

在任意目录启动 `claude`，提问时点明文档：

> Using the vestigium tools, read gao-26-107790. What lesson did service providers learn about infant formula during Hurricane Helene? Cite the PDF pages.

（这个问题的意思是：飓风 Helene 期间，服务机构在婴儿配方奶方面学到了什么教训？请写出 PDF 页码。我们只用英文提问测过。）

好的答案会说：服务机构学到要优先准备即饮的瓶装配方奶，而不是罐装奶粉，因为飓风切断了电力和安全饮用水，捐来的奶粉罐用不上，只好扔掉。出处是 PDF 第 5 页；也可能同时引用第 1 页，报告的“要点”里提到了同一条教训。

Claude Code 第一次调用 Vestigium 的工具时会请求授权。想一次放行全部 Vestigium 工具，就在 Claude Code 设置的 `permissions.allow` 里加上这条规则：

```json
{ "permissions": { "allow": ["mcp__vestigium__*"] } }
```

## 第一次运行大概是什么样

发布前，我们在全新安装的环境里把上面几步走了一遍。那次是在项目改成现在这个名字之前跑的；之后只改了名字和命令，功能没有变。

- 不带 `[ingest]` 的安装约 15 秒，样例入库约 5 秒，质检通过。
- 在 Claude Code 里用 Claude Haiku 问了 3 道关于样例的问题，对照 PDF 核对，3 题都对。
- 每题 8–18 秒，3 题按标价合计约 $0.07。

## 怎样得到更好的答案

下面几条来自我们在测试中看到的情况。把它们写进提问里能不能让答案变好，我们没有测过。

- **点明文档**，或者让助手先调用 `list_documents`。
- **要求写出 PDF 页码**，方便你回去核对。
- **列举题要求逐项列全**。我们看到的错误大多是漏项，而不是事实写错。
- **一个会话问一道题**，通常比在一个长会话里连问多题更省。
- **按需要选模型**：最看重准确用 Opus，最看重花费用 Haiku。见[结果](RESULTS_ZH.md)。
