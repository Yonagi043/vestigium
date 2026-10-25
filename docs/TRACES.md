# Traces: how a document was read

[中文](TRACES_ZH.md) · [Reading language](LANGUAGE.md) · [Rules](RULES.md) · [README](../README.md)

Vestigium records every tool call on your machine. Your agent can then answer questions such as "how did you read the report last time?", "where did you stop?" and "which signals did you not follow?", by reading the record instead of guessing.

## Turning it on

- **Recording** is on by default. Set `TGLW_TRACE=0` to turn it off; then nothing is recorded or written.
- **The `trace` tool**, which lets your agent look at the record, is off by default in this version. Turn it on when you connect the server:

```bash
claude mcp add --env TGLW_TRACE_TOOL=1 --transport stdio vestigium -- vestigium mcp
```

With the tool off, the six tools behave exactly as without it.

## Asking about a reading

Ask your agent in your own words, for example *"How did you read the GAO report last time? Which signals did you ignore?"* The agent calls `trace`:

| Parameter | Default | Meaning |
|---|---|---|
| `last` | 1 | How many recent readings to show, newest first. One reading is one conversation with the server |
| `ignored` | false | Show only the end of each reading and the signals no step followed |
| `doc` | all | Only readings of this document |
| `help` | false | Return the full description of the reading language instead (see [Reading language](LANGUAGE.md#the-full-text)) |

A real example, from two calls on the sample document (a search, then a read of its first hit):

```text
reading 1 · doc gao-26-107790 · 2 steps
1   find "FEMA staffing gap May 2023" -> hits 5 on p6 p9 p2 · 418t
2   read t111 <- hit 1#1 -> p5-7; figure fig8; tool +p6-7 (grow) 567t; tool +p5 (adjacent) 681t; tool fig-hints 11t · 1427t
stop after 2 · read p5-7 · sections s6 s5 · 1845 tokens
```

How to read it:

- **Step 1** searched by meaning and got 5 hits; the top ones are on pages 6, 9 and 2. A hit is only a pointer: those pages were not read. The reply cost 418 tokens.
- **Step 2** read block `t111`. Its address came from the first hit of step 1 (`<- hit 1#1`). It showed pages 5 to 7 and mentioned figure `fig8`.
- **`tool …`** marks what Vestigium did by itself, without being asked, and what it cost: it added the rest of pages 6–7 (`grow`), the neighbouring page 5 because it ranked high in the search (`adjacent`), and a hint after the figure. Rules, once you have some, appear the same way as `rule r-… `.
- **The last line** says where the reading stopped, which pages were actually read (their text or image came back), which sections they belong to, and the total tokens the tools returned.
- With `ignored=true`, the reply also lists signals no later step followed, for example `ignored: continues p151 (3)`: step 3 said the section went on at page 151, and nothing read it.

## What a trace cannot show

The server sees tool calls only. It does not see the model's thinking, the notes it keeps, or its final answer, so a trace ends with "stop after N" rather than with the answer. It records facts, not intentions: it does not say whether a search was a rewording or a check.

## What is stored, and where

- **Only on your machine.** Traces are files in `$VESTIGIUM_HOME/traces` (by default `~/.vestigium/traces`), one file per server process. Nothing is uploaded, and it works offline.
- **Who can read them.** The files are readable only by you; the folder is made private when Vestigium creates it.
- **What is in them.** The tool and its arguments (document name, addresses, and your agent's search words), the pages and sections shown, hit addresses, counts, token counts, signals, and what the tool added by itself.
- **No document text.** Passages, excerpts and hit sentences are not stored.
- **Search words.** These are your agent's words, but they can quote a phrase from the document. Set `TGLW_TRACE_QUERY=0` to store only their length and a hash. One exception for now: if you add the optional `add absence` or `add coverage` rules, their entries in the trace keep the search words and spellings as they are, even with `TGLW_TRACE_QUERY=0`.
- **Size.** All traces together are capped at 100 MB by default (`TGLW_TRACE_MAX_MB`); the oldest files are deleted first.
- **Deleting.** Delete the `traces` folder at any time, or set `TGLW_TRACE=0` to stop recording.
- **No effect on answers.** Recording does not change what the tools return: on our test documents, we replayed 1,869 recorded tool returns with recording off, on, and with the `trace` tool on, and all were byte-for-byte the same.
