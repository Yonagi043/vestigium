# 各客户端的接法

[English](CLIENTS.md) · [README](../README_ZH.md)

Vestigium（迹）是一个 MCP 服务，有两种运行方式：

- **本地（stdio）**：由软件自己启动 `vestigium mcp`，每个会话一份。我们就是这样测的。
- **常驻的 HTTP 服务**：你先启动一次 `vestigium mcp --http`，它预先加载好文档，各个软件连到本机的一个地址。这样每开一个新会话，就省掉了启动的等待。

只有 Claude Code 的 stdio 方式在答题中实测过。HTTP 模式是这一版新加的。其他软件的配置是照各自的官方文档写的，没有实测。

## Claude Code（stdio 方式实测过）

**本地：**

```bash
claude mcp add --scope user --transport stdio vestigium -- vestigium mcp
```

要传设置，就在名字前面加 `--env`，比如 `--env VESTIGIUM_HOME=/path/to/library` 或 `--env TGL_ALLOWED_DOCS=doc1,doc2`。

**和项目一起共享。** `--scope project` 会把服务写进项目根目录的 `.mcp.json`，可以提交进版本库：

```json
{
  "mcpServers": {
    "vestigium": {
      "type": "stdio",
      "command": "vestigium",
      "args": ["mcp"],
      "env": { "VESTIGIUM_HOME": "/path/to/library" }
    }
  }
}
```

**常驻的 HTTP 服务：**

```bash
vestigium mcp --http                                  # http://127.0.0.1:8765/mcp，只对本机开放
claude mcp add --transport http vestigium http://127.0.0.1:8765/mcp
```

- `--port` 改端口，`--host` 改监听地址（默认只对本机开放）。
- **没有访问控制。** HTTP 服务没有登录验证。默认只监听 127.0.0.1，并有防 DNS 重绑定的保护。如果你用 `--host` 让别的机器也能连上，那么谁能连上，谁就能读你的整个文档库；只在你信得过的网络里这样做。
- `--preload all|none|名字,…` 选启动时先加载哪些文档。`all` 按登记顺序加载，直到文档缓存的内存预算用完（`TGLW_CACHE_MB`，默认 2048 MB）；文档多时调大它。
- **启动耗时**（13 份文档，每份 3 轮，每轮 8 次工具调用）：每次新起一个 stdio 服务，第一次返回的中位数是 1.51 秒，8 次调用合计 2.26 秒；预先加载好的 HTTP 服务分别是 0.02 秒和 0.22 秒。启动时加载这 13 份文档要 15.5 秒，只付一次。每道题约少等 2 秒，约是 Haiku 一道题用时的 10%（估算）。
- **会话。** 服务默认用会给每个客户端分配会话的 MCP 握手方式，Claude Code 2.1.283 就是这样连上的，所以和 stdio 的行为一样。加 `--new-protocol` 时，用 2026-07-28 版协议的客户端拿不到会话，有两项功能就不起作用：附上和你的搜索相关的相邻页，以及提示改用字面检索。
- 我们的答题测试用的是 stdio。HTTP 模式核对过能连上、能返回，但没有在答题测试里用过。

**检查和授权：**

- `claude mcp list` 显示服务能不能连上；会话里用 `/mcp` 也行。
- 不想每次调用 Vestigium 都被问一遍，就在设置的 `permissions.allow` 里加上 `"mcp__vestigium__*"`。

**输出大小。** 工具结果超过 1 万 token 时 Claude Code 会警告，默认在 2.5 万 token 处截断，超过 5 万字符的文字结果会存成文件。Vestigium 把 `read` 控制在 8,000 token、40,000 字符以内，所以结果能完整送到。

服务名用 `vestigium` 时，模型看到的工具名是 `mcp__vestigium__read` 这样的。我们测试时服务名是 `tgl`，模型看到的是 `mcp__tgl__read`；评测工具保留了这个名字，好让重跑的结果和我们的一致。

## VS Code + GitHub Copilot（没有实测）

在工作区里加一个 `.vscode/mcp.json`：

```json
{
  "servers": {
    "vestigium": {
      "type": "stdio",
      "command": "vestigium",
      "args": ["mcp"],
      "env": { "VESTIGIUM_HOME": "/path/to/library" }
    }
  }
}
```

用命令 **MCP: List Servers** 启动服务。连常驻的 HTTP 服务时，把 `command` 换成 `"type": "http"` 和 `"url": "http://127.0.0.1:8765/mcp"`。

要注意：

- **输出上限。** VS Code 会把超过 8,192 字符的单段文字挪进文件，只给模型一个指向文件的提示（设置项 `github.copilot.chat.agent.largeToolResultsToDisk`）。不做处理的话，Vestigium 约三分之一的 read 会碰上。Vestigium 在连接时认出 VS Code，把每次返回切成每段不超过 8,000 字符的几段，这样模型拿到的内容应该和在 Claude Code 里一样，也不用多来回。这是按 VS Code 处理工具结果的方式推出来的，我们没有在 VS Code 里实测。
- **企业版和商业版。** Copilot Business 和 Enterprise 的组织策略 “MCP servers in Copilot” 默认是关的，要管理员打开；或者改用下面的[文件版导出](#不支持-mcp-的软件)。Copilot Free、Pro、Pro+、Max 不受这条策略管。
- **远程机器。** 如果 VS Code 通过 Remote-SSH 连到另一台机器，配置要放在远端的工作区里，服务才会在文档所在的机器上运行。
- **图片。** `region` 的图片只会交给能看图的模型。

## Cursor（没有实测）

在项目里加 `.cursor/mcp.json`，或者为所有项目加 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "vestigium": {
      "command": "vestigium",
      "args": ["mcp"],
      "env": { "VESTIGIUM_HOME": "/path/to/library" }
    }
  }
}
```

连常驻的 HTTP 服务时，把 `command` 换成 `"url": "http://127.0.0.1:8765/mcp"`。Cursor 会把大的工具结果存成文件，具体多大才存，我们不清楚。如果看到 Vestigium 的结果被存成了文件，启动服务时设 `TGLW_MCP_LIMITS=vscode`，改成切小段返回。

## 其他 MCP 客户端

按协议，能通过 stdio 启动本地命令的客户端应该能运行 `vestigium mcp`，支持 MCP streamable HTTP 的客户端应该能连常驻服务。我们只在 Claude Code 里测过。请查一下你的客户端对工具结果大小的限制：Vestigium 一次最多返回约 40,000 字符。

- **GitHub Copilot CLI**：工具输出超过 20 KiB 就存成临时文件。Vestigium 会认出它，把每次返回控制在 18,000 字节以内；`read`、`context` 读不完就先停，结尾用 `next: …` 接着读。
- **其他客户端**：用 Claude Code 的上限。想强制返回得小一些，设 `TGLW_MCP_LIMITS=vscode`（每段 8,000 字符）或 `copilot`（18,000 字节，用 `next:` 续读），见[工具说明](TOOLS_ZH.md#开关)。

## 不支持 MCP 的软件

如果你的软件用不了 MCP 服务，可以把文档导出成普通文件，让 AI 助手用它自带的读文件工具去读：

```bash
vestigium export gao-26-107790 --out tg --as gao
```

文件夹名请取短一点（用 `--as`）：每条搜索结果都会带上文件夹路径，路径太长，软件就容易把结果转存成文件。请导出到助手的工作目录里。`vestigium export --all --out tg` 导出全部文档。

导出的内容：

| 文件 | 内容 |
|---|---|
| `tg/README.md` | 怎么读这些导出文件，以及已导出的文档清单 |
| `tg/gao/INDEX.md` | 目录，每节一行：地址、节路径、PDF 页码（印刷页码）、大小，以及这一节在哪个文件的第几行到第几行。先读它 |
| `tg/gao/01-s1.md`、`02-s8.md`、…… | 按阅读顺序、按节切开的正文，写法和 `read` 一样：`== p12 ==` 表示 PDF 第 12 页开始，`## s7 · …` 表示 s7 节开始，表格就在正文里。每行最多 400 字 |
| `tg/gao/TABLES.md` | 每张表：地址、页码、大小，以及它在正文文件里的位置 |
| `tg/gao/FIGURES.md` | 每张图：地址、页码、所在节和图题（不导出图片） |
| `tg/gao/index.json` | 同样的地图，给程序用 |

建议的提示词（还没有用助手实测过）：

> The folder tg/gao/ holds the PDF "Disaster Response: Lessons Learned in Supporting Mothers and Young Children" as text files. Read tg/gao/INDEX.md first, then Read only the lines it gives for the sections you need (or Grep tg/gao/ for exact words and Read around the hit). Cite PDF pages from the "== pN ==" lines.

也可以只告诉助手先读 `tg/README.md`，里面写了同样的步骤。

可选参数（默认值就是我们核对过的设置；详见 `vestigium export --help`）：

| 参数 | 默认 | 作用 |
|---|---|---|
| `--width N` | 400 | 每行最多几个字符。400 和正式确认里整篇文本对照组的折法一致，也在 Claude Code 的 Grep 500 字上限之内。只有确认你的外壳限制更宽时才调大 |
| `--table-file-tokens N` | 不设 | 超过 N token 的表挪到 `tables/<地址>.md`，正文里只留一行指向它。默认所有表都留在正文里：回放显示，表挪出去以后，搜到表格行时就看不到上下文了 |
| `--file-tokens N` | 4000 | 每个正文文件大约放多少 token |

我们知道的和不知道的：

- 导出版保留了 Vestigium 的结构和页码，但没有工具带来的那些东西：按相关度排序的搜索、输出上限、context 和截图。
- 我们按 Claude Code 隐藏长行、把大结果转存成文件的规则，在导出文件上重放了 1,034 次以前记录下来的搜索。在同一批数据上，导出版比我们早先的原型略好：被转存的结果少一点（138 次对 139 次），送到的证据句多一点（55.3% 对 54.9%）。
- 这次重放只按 Claude Code 的限制来算；别的软件的读文件工具各有各的限制。
- 在 10 道开发题上用 Haiku 做的小检查里，文件版通过了事先定的规则（不比单个整篇文本文件低 4 个点以上），所以没有 MCP 时，我们推荐用它代替整篇文本文件。它的花费约是通过 MCP 用 Vestigium 的两倍、整篇文本的 0.6。所以：能用 MCP 就用 MCP，用不了再用文件版。见[结果](RESULTS_ZH.md#没有-mcp-时文件版导出)。
