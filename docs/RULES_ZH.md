# 规则：以后该怎么读

[English](RULES.md) · [阅读语言](LANGUAGE_ZH.md) · [轨迹](TRACES_ZH.md) · [README](../README_ZH.md)

一条规则告诉 Vestigium（迹）：出现某个信号时做什么、对哪些文档做、在什么限度内做。之后每次阅读，Vestigium 都会自己去做，每做一次，轨迹里都写明。

## 一条规则长什么样

一条规则就是[阅读语言](LANGUAGE_ZH.md)里的一行。比如，想让港交所文件的一节接到下一页时总是接着读：

```rules
type filings = hkex-*
on continues where same-section in type filings do add next-page max 1 budget 2000t
```

- `type filings = hkex-*`：按文档在文档库里的名字，给一组文档起个名字。
- `on continues`：信号，返回里说这一节接到另一页还没完。
- `in type filings`：范围。每条规则都必须写明管哪些文档：`in all`、`in doc 文档名` 或 `in type 类型名`。
- `do add next-page`：动作，Vestigium 把那一页加进同一次返回。
- `max 1` 和 `budget 2000t`：上限，每次阅读最多做一次，每次调用最多多加 2,000 token。

规则是做事的，没有只提建议的规则。“提醒模型”这类规则会被直接拒绝。

## 让 AI 助手写规则

规则不用你自己写。告诉 AI 助手哪里出了问题、你想要什么，比如：*“港交所的文件，你老是没注意到一节接到了下一页。以后遇到这种情况，把下一页也读上。”* 助手写好规则，Vestigium 先检查，没问题才留下。

写错的规则会被拒绝，并指出错在第几列，再给一个改对的例子。下面是真实的返回：

```text
on continue in all do add next-page
  col 4: unknown signal 'continue' (did you mean 'continues'?); signals: read (a read returned text), continues (the page says its section goes on), cut (a read stopped at its budget; next: lists the rest), zero (a search found nothing), saturated (searches keep returning hits seen before), view (an image came back) -- e.g. on continues in all do add next-page
on continues do add next-page
  col 14: every rule says which documents it is for: 'in all', 'in doc NAME' or 'in type NAME' before 'do' -- e.g. on continues in all do add next-page
on continues in all do remind the model to read on
  col 24: rules act, they do not 'remind': the tool does the action itself; for continues (the page says its section goes on): add next-page, add first-line -- e.g. on continues in all do add next-page
```

全部信号、条件和动作，见[阅读语言](LANGUAGE_ZH.md#完整说明)。

## 规则写错了会怎样

写错的规则不会留下。`rules` 工具会拒绝它，指出错在哪里、该怎么写，AI 助手照着再写一次。在一段对话里第一次被拒时，报错后面会附一份简短的速查，列出所有信号和动作；之后再写错，就只给报错。下面是一段真实的往来，助手忘了写这条规则管哪些文档：

```console
> rules action=add text="on cut do add rest"
col 8: every rule says which documents it is for: 'in all', 'in doc NAME' or 'in type NAME' before 'do' -- e.g. on cut in all do add rest

Quick reference: on SIGNAL [where COND] in all|type T|doc D do ACTION [max N] [budget Nt]
  read       a read returned text -> widen N | add next-page | add prev-page | add image (where scan|figure|table)
  continues  the page says its section goes on -> add next-page | add first-line
  cut        a read stopped at its budget; next: lists the rest -> add rest | add unread-headings
  zero       a search found nothing -> add absence
  saturated  searches keep returning hits seen before -> add coverage
  view       an image came back -> zoom xN
  COND: same-section from-hit scan figure table has-id pages<=N tokens<=N searches>=N
  change: text 'ID: new rule'; on, off: an id or all; why: the user's reason

> rules action=add text="on cut in all do add rest"
added r-25a1: on cut in all do add rest
```

## 先试一试：预演

留下一条规则之前，AI 助手可以先预演。Vestigium 用最近几次阅读的轨迹，加上这条规则重放一遍，列出它会在哪几步起作用、会加什么、多花多少 token，以及后面哪几步读的正是它加的内容。不调用模型，只读你这台机器上的文档库和轨迹。

一条规则可能让阅读更省，因为模型少来回几轮；也可能更贵，因为每次返回带的内容多了。预演给出多花的 token；后面读了同样内容的那几步，提示它可能省掉几轮。模型实际会不会少走这几轮，要用起来才知道。

## 管理规则

AI 助手通过 `rules` 工具管理规则。这一版里这个工具默认是关的，接入服务时打开：

```bash
claude mcp add --env TGLW_RULES_TOOL=1 --transport stdio vestigium -- vestigium mcp
```

它的参数是 `action`（下面的某个动作）、`text`（一条规则、一条规则的 id，改规则时写 `ID: 新规则`；on 和 off 写 id 或 `all`）和 `why`（用户的理由，随这次改动一起记下）。它能做这些事：

| 动作 | 做什么 |
|---|---|
| list | 列出生效的规则：每条是谁、什么时候、为什么加的，开着还是关着；末尾列出[建议](#从你的阅读里提出的建议) |
| add | 加一条规则或一个文档类型，或者采纳一条建议（`s-…`） |
| change | 改一条规则。改推荐规则时，推荐的那条会被关掉，你的版本另存为一条新规则，有自己的 id |
| delete | 删一条规则。推荐规则不会真删，而是关掉：它仍然留在列表里，可以再打开 |
| on、off | 打开或关掉某一条（写 `all` 就是全部）；`off s-…` 拒绝一条建议 |
| preview | 在最近几次阅读上预演，见上一节 |
| undo | 撤销最近一次改动；连着用就一步步往回退 |
| history | 看最近的改动 |
| reset | 恢复出厂：删掉你加的规则，推荐规则回到发布时的样子 |
| all-off | 关掉全部规则 |
| help | 阅读语言的完整说明 |

- **撤销**对每种改动都有效，包括恢复出厂和全部关掉。至少最近 200 次改动都能撤销；撤销本身也算一次改动。
- **全部关掉**就是一条规则都不起作用，Vestigium 回到还没有规则时的样子。
- **还被生效的规则用着的文档类型**不能删、不能改名，也不能关掉；返回里会列出是哪几条规则在用，先改掉或删掉它们。
- **一个类型名只能定义一次。** 再定义一个同名的类型会被拒绝，请去改已有的那条。
- **不管这个工具开没开，生效的规则都会执行。** 工具只是让 AI 助手能看到、能改规则。想在完全没有规则的情况下运行（比如做评测），设 `TGLW_RULES=0`。

下面是在样例文档上的一段真实操作：AI 助手先加一个文档类型和一条规则；之后一次读取被规则加上了下一页，标着规则的 id，轨迹里也记下了；最后试了全部关掉、撤销和恢复出厂。较长的返回有删节。

```console
> rules action=add text="type gao = gao-*" why="the GAO reports in this library"
added r-825d: type gao = gao-*
> rules action=add text="on continues where same-section in type gao do add next-page max 1 budget 2000t" why="the user asked to read on when a section continues"
added r-6949: on continues where same-section in type gao do add next-page max 1 budget 2000t

> read doc=gao-26-107790 addrs=["p5"]        (the reply, page markers only)
== p5 ==
== p4 ==
continues: p5 (s5)
== p6 ==
[rule r-6949: add next-page]
== p7 ==

> trace last=1
reading 1 · doc gao-26-107790 · 2 steps
1   find "FEMA staffing gap May 2023" -> hits 5 on p6 p9 p2 · 418t
2   read p5 <- new -> p4-7; figure fig8 fig2 fig3; table tab1; tool +p4 +p6 (adjacent) 1198t; tool fig-hints 11t; rule r-6949 add next-page +p7 717t · 2580t
stop after 2 · read p4-7 · sections s5 s6 s4 · 2998 tokens

> rules action=history
2 · 2026-10-24 09:57 · add r-6949 · why "the user asked to read on when a section continues"
1 · 2026-10-24 09:57 · add r-825d · why "the GAO reports in this library"
> rules action=all-off why="check without rules"
all rules off: the tools work as without rules
> rules action=undo
undid change 3 (all-off, by agent)
> rules action=reset why="back to factory"
factory rules restored: your rules and changes removed (undo brings them back)
…
```

## 从你的阅读里提出的建议

Vestigium 还会根据你机器上记下的阅读，提出规则建议。建议本身永远不会自己生效：`rules` 工具列规则时，会在末尾 “suggested from your readings (not active)” 下面列出建议，由 AI 助手来问你要不要。

- **什么算证据。** 某个信号当时没有跟，但后来证明它指的东西是需要的：在同一次阅读里后来还是读了；或者对“一节没完”来说，在同一份文档的另一次阅读里读了下一页。
- **只看重复出现的证据。** 同一类文档上至少出现 3 次、涉及至少 2 次阅读，才会提出建议；从不根据一次阅读提。已经生效的规则不会再被建议。
- **每条建议列出什么。** 它的编号（`s-…`）、要加的那条规则、一行证据，以及在它来源的那几次阅读上的预演。
- **采纳或拒绝。** `add s-…` 把它加上，改动历史里会记成“根据你的阅读提出、用户同意”。`off s-…` 拒绝它，以后不再提；恢复出厂会忘掉这些拒绝，撤销可以把一次拒绝改回来。

## 几条规则都适用时，听谁的

- 范围最窄的那条起作用：管一份文档的规则优先于管一类文档的，管一类文档的优先于管全部文档的。
- 范围一样时，你的规则替代推荐规则。
- 这段对话里已经显示过的页，不会再送一遍。
- 所有规则加在一起，一次调用最多多加 4,000 token。放不下的就不加，轨迹里会写明。

## 推荐规则

Vestigium 的某个版本可以随发布带上推荐规则，在你改动之前它们都起作用。每一条都可以像你自己的规则一样关掉或修改。升级以后，新的推荐规则开始起作用；你关掉的仍然关着，你改过的保留你的版本。你关掉或改过的推荐规则如果有了新版，列表里会标出来，AI 助手可以问你要不要换。

0.1.1 版带一条推荐规则：

```rules
on continues where pages<=2 in all do add next-page max 1
```

读得少（不超过两页）、而这一节接到下一页还没完时，把下一页加上，每次阅读最多一次。不管 `rules` 工具开没开，它都会执行；要停掉它，用工具把它关掉，或者设 `TGLW_RULES=0`。

另外两条试过，默认不开：在我们的测试里，它们各多花约 0.3%，没看到模型因此少搜几轮。想用的话可以自己加：

```rules
on zero in all do add absence
on saturated where searches>=5 in all do add coverage
```

这条发布的规则在测试里带来了什么变化，见[结果：0.1.1](RESULTS_0_1_1_ZH.md)。

## 存了什么，存在哪里

- 规则只存在你的机器上，在 `$VESTIGIUM_HOME/rules`（默认 `~/.vestigium/rules`）：规则原文；谁、什么时候、为什么加的；以及用于撤销的改动记录。
- 文件只有你自己能读，不上传任何东西。
- 规则里没有文档原文，只有规则本身、文档名和名字的匹配模式，以及 AI 助手写下的理由。
