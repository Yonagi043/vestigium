# Agent apps

[中文](CLIENTS_ZH.md) · [README](../README.md)

Vestigium is an MCP server. It can run in two ways:

- **Local (stdio).** The app starts `vestigium mcp` itself, one copy per session. This is how we tested it.
- **Long-running HTTP server.** You start `vestigium mcp --http` once; it loads documents in advance, and apps connect to a local address. This avoids the start-up delay of each new session.

Only Claude Code over stdio has been tested on questions. The HTTP mode is new in this release. The other configurations follow each app's documentation and are untested.

## Claude Code (tested over stdio)

**Local:**

```bash
claude mcp add --scope user --transport stdio vestigium -- vestigium mcp
```

To pass settings, add `--env` before the name, for example `--env VESTIGIUM_HOME=/path/to/library` or `--env TGL_ALLOWED_DOCS=doc1,doc2`.

**Shared with a project.** `--scope project` writes the server into `.mcp.json` at the project root, which you can commit:

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

**Long-running HTTP server:**

```bash
vestigium mcp --http                                  # http://127.0.0.1:8765/mcp, this machine only
claude mcp add --transport http vestigium http://127.0.0.1:8765/mcp
```

- `--port` changes the port and `--host` the address it listens on (default: this machine only).
- **No access control.** The HTTP server has no login. By default it listens only on 127.0.0.1, with protection against DNS rebinding. If you use `--host` to make it reachable from other machines, anyone who can connect can read your whole library; do this only on a network you trust.
- `--preload all|none|NAME,…` chooses which documents to load at start. `all` loads documents in register order until the memory budget for document caches is used up (`TGLW_CACHE_MB`, default 2048 MB); raise it for a large library.
- **Start-up time**, measured on 13 documents (3 rounds each, 8 tool calls per round): a fresh stdio server took a median of 1.51 s to its first reply and 2.26 s for all 8 calls; the preloaded HTTP server took 0.02 s and 0.22 s. Loading the 13 documents at start took 15.5 s, paid once. That saves about 2 s of waiting per question, roughly 10% of a typical Haiku question (an estimate).
- **Sessions.** By default the server uses the MCP handshake that gives each client a session, and Claude Code 2.1.283 connects that way, so everything works as over stdio. With `--new-protocol`, clients on the 2026-07-28 protocol get no session, and two features stop working: adding a neighbouring page that matched your searches, and the hint to switch to literal search.
- Our answer tests used stdio. HTTP mode was checked to connect and respond, but was not used in those tests.

**Checks and permissions:**

- `claude mcp list` shows whether the server connects; `/mcp` does the same inside a session.
- To stop Claude Code asking before each Vestigium call, add `"mcp__vestigium__*"` to `permissions.allow` in your settings.

**Output size.** Claude Code warns when a tool result exceeds 10,000 tokens, cuts it at 25,000 tokens by default, and saves text results over 50,000 characters to a file. Vestigium keeps `read` under 8,000 tokens and 40,000 characters, so results arrive in full.

With the server name `vestigium`, the model sees the tools as `mcp__vestigium__read` and so on. Our tests registered the server as `tgl`, so the model saw `mcp__tgl__read`; the evaluation tools keep that name so that reruns match ours.

## VS Code with GitHub Copilot (untested)

Add `.vscode/mcp.json` to your workspace:

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

Start it with the command **MCP: List Servers**. For the HTTP server, use `"type": "http"` and `"url": "http://127.0.0.1:8765/mcp"` instead of `command`.

Things to know:

- **Output limit.** VS Code moves any single piece of text over 8,192 characters into a file and gives the model only a pointer to it (setting `github.copilot.chat.agent.largeToolResultsToDisk`). Without a fix, about one in three Vestigium reads would be affected. Vestigium recognizes VS Code when it connects and splits each reply into pieces of at most 8,000 characters, so the model should get the same content as in Claude Code without extra rounds. This follows from how VS Code handles tool results; we have not tested it in VS Code.
- **Business and Enterprise plans.** For Copilot Business and Enterprise, the organization policy "MCP servers in Copilot" is disabled by default. An administrator must enable it, or use the [file export](#apps-without-mcp) below. Copilot Free, Pro, Pro+ and Max are not affected.
- **Remote machines.** If VS Code connects to another machine (Remote-SSH), put the configuration in the remote workspace so that the server runs where the documents are.
- **Images.** `region` pictures reach only models that accept images.

## Cursor (untested)

Add `.cursor/mcp.json` to your project, or `~/.cursor/mcp.json` for all projects:

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

For the HTTP server, use `"url": "http://127.0.0.1:8765/mcp"` instead of `command`. Cursor stores large tool results in files; we do not know the size at which it does so. If you see Vestigium results moved into files, start the server with `TGLW_MCP_LIMITS=vscode` to get the smaller pieces.

## Other MCP clients

By the protocol, a client that can start a local command over stdio should be able to run `vestigium mcp`, and a client that speaks MCP over streamable HTTP should be able to connect to the long-running server. We have tested only Claude Code. Check your client's limit on tool result size: Vestigium replies can be up to about 40,000 characters.

- **GitHub Copilot CLI** saves tool output over 20 KiB to a temporary file. Vestigium recognizes it and keeps each reply under 18,000 bytes; a longer `read` or `context` stops early and ends with `next: …` to continue.
- **Any other client** gets Claude Code's limits. To force smaller replies, set `TGLW_MCP_LIMITS=vscode` (pieces of 8,000 characters) or `copilot` (18,000 bytes, continued with `next:`); see [Tools](TOOLS.md#switches).

## Apps without MCP

If your app cannot use MCP servers, export a document as plain files and let the agent read them with its own file tools:

```bash
vestigium export gao-26-107790 --out tg --as gao
```

Use a short folder name (`--as`): every search hit repeats the folder path, and long paths make the app move results into files. Export into the agent's working folder. `vestigium export --all --out tg` exports every document.

This writes:

| File | Contents |
|---|---|
| `tg/README.md` | How to read the exports, and the list of exported documents |
| `tg/gao/INDEX.md` | Table of contents, one line per section: address, section path, PDF pages (printed pages), size, and the file and line range that hold it. Read this first |
| `tg/gao/01-s1.md`, `02-s8.md`, … | The text in reading order, split by section, in the same form as `read`: `== p12 ==` starts PDF page 12, `## s7 · …` starts section s7, tables are inline. Lines are at most 400 characters |
| `tg/gao/TABLES.md` | Every table: address, page, size, and where it is in the text files |
| `tg/gao/FIGURES.md` | Every figure: address, page, section and caption (no images) |
| `tg/gao/index.json` | The same map for programs |

A suggested prompt (not yet tested with agents):

> The folder tg/gao/ holds the PDF "Disaster Response: Lessons Learned in Supporting Mothers and Young Children" as text files. Read tg/gao/INDEX.md first, then Read only the lines it gives for the sections you need (or Grep tg/gao/ for exact words and Read around the hit). Cite PDF pages from the "== pN ==" lines.

You can also just tell the agent to read `tg/README.md` first; it gives the same steps.

Optional settings (the defaults are what we checked; see `vestigium export --help`):

| Option | Default | Effect |
|---|---|---|
| `--width N` | 400 | Longest line, in characters. 400 matches the text baseline of our confirmation and stays under Claude Code's 500-character Grep limit. Raise it only if your app's limit is wider |
| `--table-file-tokens N` | not set | Move tables larger than N tokens to `tables/<address>.md`, leaving a pointer in the text. By default every table stays in the text: in the replay, tables moved out lost their context when a search hit one of their rows |
| `--file-tokens N` | 4000 | About how many tokens go in each text file |

What we know and do not know:

- The export keeps Vestigium's structure and page numbers, but loses what the tools add: ranked search, size limits, context and pictures.
- We replayed 1,034 recorded searches against the export under Claude Code's rules for hiding long lines and moving large results into files. The export did slightly better than our earlier prototype on the same data: fewer results moved into files (138 against 139) and more evidence sentences shown (55.3% against 54.9%).
- That replay used Claude Code's limits only; other apps' file tools have their own limits.
- In a small check on 10 development questions with Haiku, the export passed the rule fixed in advance (no more than 4 points below a single whole-text file), so where MCP is not available we recommend it instead of a whole-text file. It cost about twice as much as Vestigium over MCP, and 0.6 of the whole text. So: use MCP where you can, and the export where you cannot. See [Results](RESULTS.md#without-mcp-the-file-export).
