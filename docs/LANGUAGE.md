# The reading language

[中文](LANGUAGE_ZH.md) · [Traces](TRACES.md) · [Rules](RULES.md) · [README](../README.md)

Vestigium has a small language for one thing: describing how a document is read. It is used in two directions.

- **Traces.** Vestigium writes down, in this language, how each reading went: which pages and sections were read, what each step was, which signals came back, and what the tool did by itself. See [Traces](TRACES.md).
- **Rules.** Your agent writes, in this language, how future readings should go, for example "when a section goes on to the next page, read that page too". Vestigium then carries the rule out itself. See [Rules](RULES.md).

You do not need to learn it. Talk to your agent in your own words; the agent reads traces and writes rules for you. This page is for checking what your agent did, and for the curious.

## Why a language, and not settings

- **Your agent is the interface.** People who use Vestigium already work through an AI agent, so there are no settings pages or buttons. The agent needs something short and exact to read and write.
- **Rules are carried out, not suggested.** A hint added to a tool's reply is easy for a model to ignore. A rule is different: the tool does the action itself, and the trace shows that it did.
- **One language both ways.** A trace and a rule use the same words, so a step that went wrong in a trace can be turned into a rule that fixes it.

## The words at a glance

- **Blocks**: what one step does. `find` searches by meaning, `grep` finds exact text and counts it, `map` looks at structure, `read` reads addresses, `view` looks at an image. `note` and `answer` belong to the model; the server does not see them.
- **Signals**: what a reply carries that might call for another step. For example `continues p151` (the section goes on), `cut next …` (a read stopped at its size limit), `zero` (an exact search found nothing), `saturated` (a search mostly repeated earlier hits), `scan` (OCR text).
- **Origins**: in a trace, where each step's address came from, such as `<- hit 1#1` (the first hit of step 1) or `<- continues 3`.
- **Rules**: `on SIGNAL … in SCOPE do ACTION`, with optional limits such as `max 1` and `budget 2000t`.
- **Control**: steps in order; calls sent together share a number (`2a`, `2b`); a rule fires every time its signal appears, up to its limit; a trace ends with where the reading stopped and which signals no step followed.

## The full text

The text below is what the server gives your agent when it asks for help (the `trace` tool with `help=true`). It is the authoritative description: if this page and the server ever differ, the server is right.

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
