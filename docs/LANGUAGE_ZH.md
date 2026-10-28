# 阅读语言

[English](LANGUAGE.md) · [轨迹](TRACES_ZH.md) · [规则](RULES_ZH.md) · [README](../README_ZH.md)

Vestigium（迹）有一门很小的语言：V 语言（Vestigium 阅读语言）。它只做一件事：描述一份文档是怎么读的。它有两个方向的用法。

- **轨迹**：Vestigium 用这门语言记下每次阅读的经过：读了哪些页和节、每一步做了什么、返回了哪些信号、工具自己替模型做了什么。见[轨迹](TRACES_ZH.md)。
- **规则**：你的 AI 助手用这门语言写下以后该怎么读，比如“一节还没完、接到下一页时，把下一页也读上”。之后由 Vestigium 自己执行。见[规则](RULES_ZH.md)。

你不需要学会它。用自己的话跟 AI 助手说就行，读轨迹、写规则都由助手来做。这一页是给你核对助手做了什么用的，也给想了解的人看。

## 为什么是一门语言，而不是设置项

- **AI 助手就是界面。** 用 Vestigium 的人本来就在通过 AI 助手工作，所以没有设置页面和按钮。助手需要的是一种又短又准、能读也能写的东西。
- **规则是执行的，不是建议的。** 在工具返回里加一句提示，模型很容易不理。规则不一样：动作由工具自己做，轨迹里写明它做了。
- **两个方向用同一门语言。** 轨迹和规则用的是同一套词，轨迹里哪一步出了问题，就可以直接写成一条规则去修。

## 这些词，一眼看过

- **积木**：一步做的事。`find` 按意思搜，`grep` 按原文找并计数，`map` 看结构，`read` 按地址读，`view` 看图。`note`（摘取）和 `answer`（作答）在模型那边，服务端看不到。
- **信号**：返回里带的、可能需要再走一步的东西。比如 `continues p151`（这一节还没完）、`cut next …`（读到大小上限被截住了）、`zero`（原文查找 0 处）、`saturated`（搜到的大多是前面列过的）、`scan`（这是 OCR 文字）。
- **来源**：轨迹里每一步的地址是从哪来的，比如 `<- hit 1#1`（第 1 步的第 1 条命中）、`<- continues 3`。
- **规则**：`on 信号 … in 范围 do 动作`，可以加上限，比如 `max 1`、`budget 2000t`。
- **控制**：步骤按先后排；同一轮一起发出的调用共用一个编号（`2a`、`2b`）；规则每遇到一次它的信号就执行一次，直到上限；轨迹最后写明在哪里停的、哪些信号后面没有一步去跟。

## 完整说明

下面是 AI 助手向服务要帮助时（`trace` 工具加 `help=true`）拿到的原文，原样照录。它是权威说明：如果这一页和服务给的说法不一样，以服务为准。

<!-- help:start -->
```text
Vestigium reading language (v0)

A trace shows how a document was read, one step per tool call.

Blocks
  find "q"     ranked search by meaning (search)
  grep "t"     exact text: every match and the count (search mode=literal)
  map X        structure around X: outline, context, list of documents, region preview
  read A       text at addresses A (read): p12, p12-14, s7, t123, tab4, fig5, 'Note 7'
  view A       image of a figure, table or page box (region); xN is the zoom
  note         taking evidence into notes (not a tool yet; not in traces)
  answer       the reply (the server does not see it)

Step line
  <n> <block> <args> [<- origin] -> <what came back> [; signal]... [; tool …] · <cost>
  n: call order. Calls sent together share a number with letters: 2a, 2b.
  origin, where the address came from:
    <- hit 2#1       hit 1 of step 2 (2#id: an exact identifier match the tool added)
    <- continues 4   the 'continues' of step 4
    <- cut 3         the 'next:' of the cut read at step 3
    <- map 1         listed by the structure of step 1
    <- near          a page next to pages already read, with no pointer to it
    <- again         nothing new: every page already read
    <- new           not in any earlier return (from the question, or a guess)
  Searches have no origin.

Signals
  hits N on pX…    a search listed N hits; pX… is where the top ones are (not read)
  count N on pX…   exact matches and where; zero: none
  saturated        most hits were listed before
  continues pN     the section goes on at page N
  cut next X       the read stopped at its budget; X is the rest
  too-long         too many addresses for one read
  scan pN, qc pN   page N is a scan (OCR text), or the ingest check doubts it
  figure F, table T  a figure or table is on the pages shown

What the tool did by itself (not asked for)
  tool +p149 (adjacent)   added a page next to what was read that ranks for the session's searches
  tool +p151 (grow)       a small block read also got its page, or the next page
  tool +count "SI-4" 78   added exact counts to a search
  tool saturation-note    added the saturated pointer
  tool zoom x2.4          chose the zoom
  rule r-7f3a …           a rule did it (rules, below; r-… is the rule's id)
  Each shows its tokens.

End
  stop after N · read … · sections … · tokens …
  read: pages whose text or image came back (hit pages are not read pages)
  ignored: continues p151 (4); cut s17 (3)   signals no later step followed
  The server sees calls only: not the model's notes, nor its answer.

Example
  1  find "food safety incident" -> hits 5 on p150 p149 p136 · 386t
  2  read p150 <- hit 1#1 -> p149-150; continues p151; tool +p149 (adjacent) 512t · 1614t
  stop after 2 · read p149-150 · 2.0k tokens
  ignored: continues p151 (2)

Rules (kept by the rules tool; the tool carries them out itself)
  on SIGNAL [where COND [and COND]] in SCOPE do ACTION[, ACTION] [max N] [budget Nt] [why "…"]
  SIGNAL and its actions:
    read       widen N (pages each side, 1-3), add next-page, add prev-page, add image
    continues  add next-page, add first-line (of the next page)
    cut        add rest, add unread-headings
    zero       add absence (what was searched, counts of the query's parts, nearest passages)
    saturated  add coverage (so far: the queries and their counts, the pages and sections read)
    view       zoom xN (above 1, at most 3; when the call gave no scale)
  COND    same-section, from-hit, scan, figure, table, has-id, pages<=N, tokens<=N, searches>=N
          add image needs where scan, figure or table: the first such image shown
          searches>=N: this reading has made N searches or more, this one included
  SCOPE   all | doc NAME | type NAME     (a type: type NAME = NAME-PATTERN, …)
  max N: times per reading (one conversation with the server) the rule added something.
  budget Nt: tokens a rule may add to one call (default 2000t; all rules together 4000t).
  Pages already shown are not sent again. Added text starts with [rule <id>: <action>].
  Rules on one signal: the narrowest scope acts (doc, then type, then all);
  at the same scope your rules replace the recommended ones.
  e.g.  type hkex = hkex__*
        on continues where same-section in type hkex do add next-page max 1 budget 2000t
  The tool carries a rule out itself and the trace shows it (rule r-… ). Rules act; none only advises.
  A wrong rule is refused with its column and a corrected example.
  rules tool: text is a rule, its id, or for change 'ID: new rule'; on and off take an id or all.
  reset: back to the release's recommended rules; all-off: no rule at all, as v1.
  list ends with rules suggested from your readings (not active): add s-… adopts one if the user agrees;
  off s-… turns it down: not suggested again until a reset (undo brings it back).
  preview replays recent readings with the rules off and on; it lists the steps they act on:
    3   read p150 <- hit 1a#1 -> p149-150; continues p151 · 1614t
        + rule r-7f3a add next-page +p151 769t
    4   read p151 <- continues 3 -> p151 · 812t · already added at 3
    total: rules act on 1 step in 1 of 2 readings · +769 tokens · 1 later step read what they added
```
<!-- help:end -->
