# 复现说明

[English](REPRODUCE.md) · [README](README_ZH.md)

这一页讲怎样用 `vestigium eval` 在公开题上重跑我们开发阶段的比较，以及正式确认是怎么做的。正式确认的题保密，所以确认本身没法重跑；其余的都可以。

这些都要花钱：每次运行都通过你自己的 Claude Code 调用模型。我们这边一道题、一种读法，从几美分（haiku 用 Vestigium）到约 30 美分（haiku 直接读 PDF）不等，判分另算。

## 1. 需要什么

- Python 3.12 或更新，`pip install "vestigium[ingest,eval] @ git+https://github.com/Yonagi043/vestigium"`（Vestigium 从仓库安装，不在 PyPI 上；`[ingest]` 会装上 Docling，用来转换 PDF）。
- Claude Code：`claude` 要在 PATH 里，或者用 `TGLW_EVAL_CLAUDE` 指定。我们用的是 2.1.283，其他版本的行为会有差别。
- poppler-utils（`pdftoppm`、`pdfimages`）：Claude Code 的 Read 工具靠 `pdftoppm` 显示 PDF 页面，读法 P 离不开它。
- 能通过 Claude Code 调用模型。模型请写完整的 id，比如 `claude-haiku-4-5-20251001`，因为你的设置可能把简称指向别的模型。

所有命令里的路径都请写绝对路径：计划表和运行目录会原样记下你给的路径。

## 2. 文档和文档库

```bash
vestigium eval fetch-docs documents.json --out "$PWD/pdfs" --ingest-script ingest.sh
bash ingest.sh        # 对每份文档运行 vestigium ingest <pdf> --name <登记名> --cls <类别>
```

`fetch-docs` 会把每个文件的 SHA-256 和 `documents.json` 比对；我们的服务器没能核对的几份见 README。登记名很重要：题目按这些名字找文档。文档库默认放在 `~/.vestigium`，设了 `VESTIGIUM_HOME` 就放在它指的目录。

你用的 Docling 版本可能和我们的不同，有些页的解析结果会不一样，所以你那边的 Vestigium 和整篇文本不会和我们的逐字节相同。

## 3. 材料

```bash
W="$PWD/texts"
vestigium eval fulltext --out "$W/w"                         # 每份文档的整篇 Docling 文本
vestigium eval fold --src "$W/w" --out "$W/w400"             # W′：每行不超过 400 字
vestigium eval snapshot --dest "$PWD/snap"                   # 冻结 Vestigium 的代码和侧车，给读法 T 用
```

- **W′**（`fold`）：Claude Code 的 Grep 会藏起超过 500 字的行。`fold` 在句末把长行切开，每行不超过 400 字（算上切口处保留的空格，最多 404），而且一个字符都不改：删掉新加的换行，就逐字还原原文。我们最早的“整篇文本”对照组没有这样做，吃了亏。
- **快照**：读法 T 从快照里启动 Vestigium 的 MCP 服务，一批运行期间什么都不会变。

## 4. 计划和运行

一张计划表包含所有读法，按种子打乱顺序：

```bash
Q="$PWD/questions/dev3.jsonl"
vestigium eval plan all --h-path "$Q" --conditions T,P,W --fulltext "$W/w400" \
    --stage dev3 --seed 1 --out "$PWD/plan_dev3.csv"
```

`--h-path` 指定题目文件（JSONL），要用几个文件就写几次。读法 W 用的是折过行的文本，也就是 W′。然后可以一次跑一个：

```bash
vestigium eval stage "$PWD/plan_dev3.csv" --dest "$PWD/eval_runs/dev3" --snapshot "$PWD/snap" --h-path "$Q"
vestigium eval headless --dest "$PWD/eval_runs/dev3" --model claude-haiku-4-5-20251001 --dry-run   # 先看一遍
vestigium eval headless --dest "$PWD/eval_runs/dev3" --model claude-haiku-4-5-20251001
```

也可以像正式确认那样分 3 路并行：

```bash
cat > spec.json <<JSON
{"dest": "$PWD/eval_runs/dev3", "lanes": 3, "seed": 2,
 "substages": [{"name": "haiku", "questions": ["$Q"], "conditions": ["T", "P", "W"],
                "model": "claude-haiku-4-5-20251001", "seed": 1, "snapshot": "$PWD/snap", "fulltext": "$W/w400"}]}
JSON
vestigium eval stage-parallel spec.json
for i in 1 2 3; do mkdir -p ~/.claude-eval-$i && cp ~/.claude/settings.json ~/.claude-eval-$i/; done
# 每一路用自己的 Claude 配置目录；如果你用账号登录 Claude Code，每个目录各登录一次
# （CLAUDE_CONFIG_DIR=~/.claude-eval-1 claude，以此类推）
vestigium eval parallel --dest "$PWD/eval_runs/dev3" --config-dir ~/.claude-eval --retry-infra
```

每次运行是这样的：

- 每次运行都是一个新的 `claude -p` 会话，在自己的目录里启动，目录里只有材料：PDF（P）、文本文件（W′），或者指向 Vestigium 服务的 `.mcp.json`（T）。T 的服务只能打开这道题的文档。
- 各读法的提示只差说明材料的那一行。提示要求用英文作答，每个事实后面用 `(p. N)` 标页码。
- 读法 T 的 `.mcp.json` 里，服务登记的键名是 `tgl`，提示里也把工具叫作 “the `tgl` MCP tools”。这两处特意保留了早先的工作名，这样你的提示和我们运行时的提示一字不差。
- 工具在启动时就定死：T 只有 Vestigium 的工具；P 和 W′ 只有 Read、Grep、Glob。IDE 连接是关掉的。
- 超过 900 秒或 60 轮就终止。遇到 API 重试、限流或报错，整批停下，等人来看；加 `--retry-infra` 时，这类基础设施故障按原材料补跑一次，运行本身的失败一律不重跑。
- 记录写在 `eval_records/dev3/`（和 `eval_runs/` 并列）：会话日志、答案、每次工具调用的审计、实测的 token 和时间，以及台账。工具调用超出了本读法（读别的文件、上网、打开别的文档）的运行记为无效。

## 5. 判分

```bash
G="$PWD/grading"; K="$PWD/keys"        # keys/ 不要让判分者看到
vestigium eval sheets "$PWD/eval_runs/dev3/haiku/plan.csv" "$PWD/eval_records/dev3/haiku" \
    --out "$G/sheets" --blind "$K/key.csv" --h-path "$Q"
vestigium eval evidence-images --h-path "$Q" --out "$G/img"          # 只有看图题会有截图
vestigium eval grading-queue "$G/sheets" --fulltext "$W/w" --images "$G/img" --seed 3 --out "$G/queue.json"
vestigium eval grader-prompt "$G/queue.json" 1                        # 第 1 个判分者的提示
```

（一次跑一个时，计划表是 `eval_runs/dev3/plan.csv`，记录目录是 `eval_records/dev3`。）

- 每张评分单放一份答案，编号随机；单上有题目、答案要点、参考答案和出题说明，还有自动提示（找到的关键串、引用的页码）。
- 队列里的每一项交给一个新的判分 agent，提示就是上面打印出来的那段。我们用的是 Claude Code 里的 Claude Opus，一题一个 agent，同时最多 4 个。判分者拿到这道题的全部答案，每份一张单，顺序随机，不知道哪份出自哪种读法；它只能读评分单、文档的整篇文本，看图题另有证据截图；不得再分出子代理。
- 把每个判分者的回复存成 JSON，写进评分单：

  ```bash
  vestigium eval fill "$G/sheets" reply_01.json --expect <这一项的编号，逗号分隔>
  ```

- 逐个查判分者的会话日志：除了列出的文件，它不应打开别的东西。
- 另找新的判分者，在评分单的新副本上重判至少 20% 的题，看两次判分的一致程度（`compare --regrade` 会算）。

## 6. 结果

```bash
vestigium eval summarize "$G"/sheets/*.csv --key "$K/key.csv" --out summary.md
vestigium eval compare "$G"/sheets/*.csv --key "$K/key.csv" --plan "$PWD/eval_runs/dev3/haiku/plan.csv" \
    --records "$PWD/eval_records/dev3/haiku" --h-path "$Q" --fulltext-index "$W/w/index.csv" --out compare.md
vestigium eval costs "$PWD/eval_runs/dev3/haiku/plan.csv" "$PWD/eval_records/dev3/haiku"
```

- `summarize`：各读法的严格、宽松准确率，整题全对，编造，不可回答题。
- `compare`：逐题配对的差（T − P、T − W），附自助法区间和符号检验；花费和用时之比；按文档类别、题型、整篇超过 200k 上下文的文档、扫描件分开报告。
- `costs`：各读法的 token、工具调用、图片、主机秒，以及 Claude Code 报的标价。

你的结果会和项目结果页上的开发数字有差别（那里的发布检查用的是 `old57` 和 `dev3` 里 8 道单文档题，看图检查用的是 `vis_dev`）：同一道题跑两次，花费的差别在我们的测试里中位是 13–23%；Claude Code 换了版本，行为也会变；而且这些开发题在开发中被看过。

## 7. 正式确认是怎么做的

正式确认用的是同一套工具，另外加了下面这些，全都写进预注册，在第一次运行之前提交：

- **新题**：29 道文字题、18 道看图题，由另一个 agent 会话出题，开发中从没用过，保密。按事先定好的规则，前 15 题判完后又补了 4 题。
- **只回答 4 个问题**：花费和用时；haiku 上 T 与 P 的准确率差；haiku 上 T 是否不比 W′ 差 5 个百分点以上（主问题）；编造。
- **读法**：文字题上 haiku 跑 T、P 和两遍 W′（两遍取均值）；看图题上 haiku 跑 T、P 和 V′（修补过的文本，同样折行）；opus（effort `xhigh`）在文字题上跑 T 和 W′。和上面一样分 3 路；用保留目录，每一路用单独的 Claude 配置目录，保密题不进平常的日志。
- **判分**：每题一个盲评的 opus 判分者，拿到这题的全部答案；凡是比较的两种读法得分不同的题，都换一个新的判分者重判，其余的题再随机抽一部分，合计不少于 20%；两次判分不一致的要点交第三个 agent，它只看题目、要点、答案和证据页。主分析用首判，裁决后的结果作敏感性检查。
- **分析**（`tglw.nextround.stats`；导入包名 `tglw` 来自项目早期的名字）：
  - 每题 d = T 的严格分减去两遍 W′ 的均值；
  - 按题重抽样 10,000 次，d 的均值的单侧 97.5% 下界（`bootstrap_lower`）高于 −5 个点，就判“不劣”；精确符号翻转检验（`exact_signflip`）给出的 p 值只作参考；
  - T − P 同样算，文字题和看图题分层重抽样；
  - 花费和用时：同一批题上两种读法合计之比，区间按整份文档重抽样（`ratio_ci_by_document`）；
  - 跑完 15 题后，用不看方向的 d² 均值重估了一次需要的题数（`needed_questions`）。
- **核对**：每次运行都做了审计，没有一次读到别的运行的记录或答案。报告出来后，另开一个 agent 自己写代码复算主要数字，结果相同。

```python
from tglw.nextround import stats          # 导入包名 tglw 来自项目早期的名字
d = [...]                                   # 每题的 T − W′，严格分，取值在 [-1, 1]
lower = stats.bootstrap_lower(d, seed=20261104)
p, method = stats.exact_signflip(d)
print(f"均值 {sum(d) / len(d):+.3f}，下界 {lower:+.3f}，p {p:.3f}（{method}）")
```

结果和局限见项目的结果页和方法页。

## 8. 用自己的题，以及设置

- 题目按 `questions/*.jsonl` 的格式写成 JSONL（见 README），`split` 写 `dev`。每个 `doc` 都要是你的 Vestigium 文档库里的登记名。
- 想保持“没人见过”的题，放在名字以 `heldout` 开头的目录里。这样不加 `--allow-heldout`，`vestigium eval` 就拒绝读；放在这种目录下的阶段目录和记录目录，两者都必须在这种目录下，并且要用名为 `.claude-heldout*` 的 Claude 配置目录，这些题的提示和日志就不会进你平常的 `~/.claude`。
- 设置都用环境变量：

| 变量 | 默认 | 作用 |
|---|---|---|
| `TGLW_EVAL_CLAUDE` | `claude` | Claude Code 的命令 |
| `TGLW_EVAL_PYTHON` | 运行 `vestigium eval` 的那个 Python | 启动读法 T 的 MCP 服务用的解释器 |
| `TGLW_EVAL_GUARD` | 无 | 逗号分隔的路径片段；运行中的工具调用提到其中任何一个就标出来（比如你放答案键的地方） |
| `TGLW_EVAL_HELDOUT` | `heldout` | 逗号分隔的目录名前缀，表示保密材料 |

- 每条命令的全部选项见 `vestigium eval <命令> --help`。
