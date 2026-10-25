# Rules: how future readings should go

[中文](RULES_ZH.md) · [Reading language](LANGUAGE.md) · [Traces](TRACES.md) · [README](../README.md)

A rule tells Vestigium what to do when a certain signal appears, for which documents, and within what limits. Vestigium then does it by itself during every later reading, and the trace shows each time it did.

## What a rule looks like

A rule is one line in the [reading language](LANGUAGE.md). For example, to always read on when a section of a Hong Kong filing continues on the next page:

```rules
type filings = hkex-*
on continues where same-section in type filings do add next-page max 1 budget 2000t
```

- `type filings = hkex-*` names a group of documents by their names in your library.
- `on continues` is the signal: the reply said the section goes on at another page.
- `in type filings` is the scope. Every rule must say which documents it is for: `in all`, `in doc NAME` or `in type NAME`.
- `do add next-page` is the action: Vestigium adds that page to the same reply.
- `max 1` and `budget 2000t` are limits: at most once per reading, and at most 2,000 extra tokens per call.

Rules act; none of them only advises. There is no "remind the model" rule: a rule like that is refused.

## Asking your agent for a rule

You do not write rules yourself. Tell your agent what went wrong and what you want, for example: *"With the Hong Kong filings you keep missing that a section goes on to the next page. From now on, read that page too."* The agent writes the rule, and Vestigium checks it before keeping it.

A rule with a mistake is refused, with the column where it went wrong and a corrected example. Real replies:

```text
on continue in all do add next-page
  col 4: unknown signal 'continue' (did you mean 'continues'?); signals: read (a read returned text), continues (the page says its section goes on), cut (a read stopped at its budget; next: lists the rest), zero (a search found nothing), saturated (searches keep returning hits seen before), view (an image came back) -- e.g. on continues in all do add next-page
on continues do add next-page
  col 14: every rule says which documents it is for: 'in all', 'in doc NAME' or 'in type NAME' before 'do' -- e.g. on continues in all do add next-page
on continues in all do remind the model to read on
  col 24: rules act, they do not 'remind': the tool does the action itself; for continues (the page says its section goes on): add next-page, add first-line -- e.g. on continues in all do add next-page
```

The full list of signals, conditions and actions is in the [reading language](LANGUAGE.md#the-full-text).

## When a rule is written wrong

Nothing is kept. The `rules` tool refuses the rule and says where it went wrong and how to write it, and the agent tries again. The first time a rule is refused in a conversation, a short quick reference of all signals and actions comes with the error; after that, only the error. A real exchange, where the agent forgot to say which documents the rule is for:

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

## Try it first: preview

Before keeping a rule, your agent can preview it. Vestigium replays your recent readings from their traces with the rule, and lists the steps it would act on, what it would add and how many tokens, and which later steps read what it added. No model is called; only your library and your traces on this machine are read.

A rule can make reading cheaper, because the model needs fewer rounds, or dearer, because each reply carries more text. The preview shows the extra tokens, and the later steps that read what the rule added hint at rounds it could save. Whether the model really skips those rounds shows only in use.

## Managing rules

Your agent manages rules through the `rules` tool. In this version the tool is off by default; turn it on when you connect the server:

```bash
claude mcp add --env TGLW_RULES_TOOL=1 --transport stdio vestigium -- vestigium mcp
```

Its parameters are `action` (one of the actions below), `text` (a rule, a rule's id, or for change `ID: new rule`; on and off take an id or `all`) and `why` (the user's reason, kept with the change). It can:

| Action | What it does |
|---|---|
| list | Show the rules in effect, with who added each, when and why, and whether it is on; then any [suggestions](#suggestions-from-your-readings) |
| add | Add a rule or a document type, or accept a suggestion (`s-…`) |
| change | Change a rule. Changing a recommended rule turns it off and saves your version as a new rule with its own id |
| delete | Delete a rule. A recommended rule is turned off instead: it stays in the list and can be turned on again |
| on, off | Turn one rule on or off (`all` for every rule); `off s-…` turns a suggestion down |
| preview | Try a rule on your recent readings, as above |
| undo | Undo the last change; repeat to go further back |
| history | Show recent changes |
| reset | Back to the factory state: your rules are removed and the recommended rules return to how they ship |
| all-off | Turn every rule off |
| help | The full description of the language |

- **Undo** works for every change, including reset and all-off. At least the last 200 changes can be undone; an undo is itself a change.
- **All off** means no rule acts at all, and Vestigium behaves as it did before rules existed.
- **A document type still used** by a rule in effect cannot be deleted, renamed or turned off; the reply names the rules that use it, to change or delete first.
- **Each type name is defined once.** Defining a type with a name already in use is refused; change the existing one instead.
- **Rules act whether or not the tool is on.** The tool is only how your agent sees and changes them. To run with no rule at all, for example in an evaluation, set `TGLW_RULES=0`.

A short real session on the sample document. The agent adds a document type and a rule; a later read gets the next page added, marked with the rule's id, and the trace shows it; then all-off, undo and reset. Long replies are shortened.

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

## Suggestions from your readings

Vestigium can also suggest rules, from the readings recorded on your machine. A suggestion is never active by itself: the list from the `rules` tool shows suggestions at its end, under "suggested from your readings (not active)", and your agent asks you about them.

- **What counts as evidence.** A signal that was not followed at the time, but whose target was wanted after all: it was read later in the same reading, or, for a section that continues, the next page was read in another reading of the same document.
- **Only repeated evidence.** A rule is suggested only when this happens at least 3 times in at least 2 readings of one type of document, never from a single reading. Rules already in effect are not suggested again.
- **What each suggestion shows.** Its id (`s-…`), the rule as it would be added, the evidence in one line, and a preview on the readings it came from.
- **Accepting or declining.** `add s-…` adds it; its history then says it was suggested from your readings and accepted by the user. `off s-…` turns it down, and it is not suggested again; a reset forgets these refusals, and undo takes a refusal back.

## Who wins when several rules apply

- The narrowest scope acts: a rule for one document beats a rule for a type, which beats a rule for all documents.
- At the same scope, your rules replace the recommended ones.
- Pages already shown in the conversation are not sent again.
- All rules together add at most 4,000 tokens to one call. Anything that does not fit is held back, and the trace says so.

## Recommended rules

A version of Vestigium can ship with recommended rules, which work until you change them. Each can be turned off or changed like your own. When you upgrade, new recommended rules start working, while the ones you turned off stay off and the ones you changed keep your version. The list marks a rule you turned off or changed when a newer recommended version of it has arrived, so your agent can ask you whether to switch.

Version 0.1.1 ships one recommended rule:

```rules
on continues where pages<=2 in all do add next-page max 1
```

When a short read (two pages or fewer) ends in a section that goes on to the next page, that page is added, at most once per reading. It acts whether or not the `rules` tool is on; to stop it, turn it off with the tool, or set `TGLW_RULES=0`.

Two more rules were tried and are not on by default, because in our tests each added about 0.3% to the cost without fewer search rounds. You can add them yourself:

```rules
on zero in all do add absence
on saturated where searches>=5 in all do add coverage
```

What the shipped rule changed in our tests: [Results: 0.1.1](RESULTS_0_1_1.md).

## What is stored, and where

- Your rules are stored only on your machine, in `$VESTIGIUM_HOME/rules` (by default `~/.vestigium/rules`): the rule text, who added it, when and why, and a history of changes for undo.
- The files are readable only by you. Nothing is uploaded.
- Rules hold no document text: only the rule lines, document names and name patterns, and the reasons your agent gave.
