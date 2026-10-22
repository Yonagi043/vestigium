# Reproducing the comparisons

[中文](REPRODUCE_ZH.md) · [README](README.md)

This page shows how to rerun our development comparisons on the public questions with `vestigium eval`, and how the formal confirmation was run. The confirmation questions are private, so the confirmation itself cannot be rerun; everything else can.

Nothing here is free: every run calls a model through your own Claude Code. One question in one setup took us from a few cents (Haiku with Vestigium) to about 30 cents (Haiku reading the PDF), plus grading.

## 1. What you need

- Python 3.12 or newer and `pip install "vestigium[ingest,eval] @ git+https://github.com/Yonagi043/vestigium"` (Vestigium is installed from its repository, not from PyPI; `[ingest]` brings Docling, which converts the PDFs).
- Claude Code (`claude` on the PATH, or set `TGLW_EVAL_CLAUDE`). We used version 2.1.283; other versions behave differently.
- poppler-utils (`pdftoppm`, `pdfimages`): Claude Code's Read tool needs `pdftoppm` to show PDF pages, which setup P depends on.
- Model access through Claude Code. Use full model ids (for example `claude-haiku-4-5-20251001`), because short aliases can be mapped to other models in your settings.

Use absolute paths in every command: plans and run directories keep the paths you give.

## 2. Documents and library

```bash
vestigium eval fetch-docs documents.json --out "$PWD/pdfs" --ingest-script ingest.sh
bash ingest.sh        # vestigium ingest <pdf> --name <name> --cls <class>, for every document
```

`fetch-docs` checks each file's SHA-256 against `documents.json`; see the README for the files our server could not check. The names matter: questions refer to documents by these names. The library goes to `~/.vestigium` unless `VESTIGIUM_HOME` names another directory.

Your Docling version may parse some pages differently from ours, so your Vestigium and your whole-text files will not be byte-identical to ours.

## 3. Materials

```bash
W="$PWD/texts"
vestigium eval fulltext --out "$W/w"                         # the whole Docling text of each document
vestigium eval fold --src "$W/w" --out "$W/w400"             # W′: no line over 400 characters
vestigium eval snapshot --dest "$PWD/snap"                   # freezes the Vestigium code and sidecars for setup T
```

- **W′** (`fold`): Claude Code's Grep hides lines longer than 500 characters. `fold` cuts long lines after sentence ends so that no line is longer than 400 characters (up to 404 counting the spaces kept at a cut), and keeps every character: deleting the added line breaks gives back the original text. Our first text baseline did not do this and was handicapped.
- **Snapshot**: setup T runs the Vestigium MCP server from the snapshot, so nothing changes during a batch.

## 4. Plan and run

One plan holds every setup, in a seeded random order:

```bash
Q="$PWD/questions/dev3.jsonl"
vestigium eval plan all --h-path "$Q" --conditions T,P,W --fulltext "$W/w400" \
    --stage dev3 --seed 1 --out "$PWD/plan_dev3.csv"
```

`--h-path` names a question file (JSONL); repeat it to use several files. Setup W uses the folded texts, so it is W′. Then either run one at a time:

```bash
vestigium eval stage "$PWD/plan_dev3.csv" --dest "$PWD/eval_runs/dev3" --snapshot "$PWD/snap" --h-path "$Q"
vestigium eval headless --dest "$PWD/eval_runs/dev3" --model claude-haiku-4-5-20251001 --dry-run   # look first
vestigium eval headless --dest "$PWD/eval_runs/dev3" --model claude-haiku-4-5-20251001
```

or in three lanes, as in the confirmation:

```bash
cat > spec.json <<JSON
{"dest": "$PWD/eval_runs/dev3", "lanes": 3, "seed": 2,
 "substages": [{"name": "haiku", "questions": ["$Q"], "conditions": ["T", "P", "W"],
                "model": "claude-haiku-4-5-20251001", "seed": 1, "snapshot": "$PWD/snap", "fulltext": "$W/w400"}]}
JSON
vestigium eval stage-parallel spec.json
for i in 1 2 3; do mkdir -p ~/.claude-eval-$i && cp ~/.claude/settings.json ~/.claude-eval-$i/; done
# each lane runs with its own Claude config directory; if you sign in to Claude Code with an account,
# sign in once in each (CLAUDE_CONFIG_DIR=~/.claude-eval-1 claude, ...)
vestigium eval parallel --dest "$PWD/eval_runs/dev3" --config-dir ~/.claude-eval --retry-infra
```

How a run works:

- Each run is a fresh `claude -p` session in its own directory, which holds only the material: the PDF (P), the text file (W′) or an `.mcp.json` for the Vestigium server (T). T's server is limited to the question's documents.
- The prompt is the same for every setup except the line that names the material. It asks for an answer in English with a `(p. N)` citation after each fact.
- In setup T the server's key in `.mcp.json` is `tgl`, and the prompt calls the tools "the `tgl` MCP tools". Both keep this earlier working name on purpose, so that your prompts are word for word the ones we ran.
- Tools are fixed at launch: T has only the Vestigium tools; P and W′ have Read, Grep and Glob. IDE connections are switched off.
- A run is killed after 900 seconds or 60 turns. Any API retry, rate limit or error stops the whole batch for a person to look; `--retry-infra` gives such an infrastructure failure one more attempt with the same material, and never reruns a run that failed by itself.
- Records go to `eval_records/dev3/` (beside `eval_runs/`): the session log, the answer, an audit of every tool call, measured tokens and time, and a ledger. A run whose tool calls left its setup (another file, the web, another document) is marked invalid.

## 5. Grading

```bash
G="$PWD/grading"; K="$PWD/keys"        # keep keys/ away from the graders
vestigium eval sheets "$PWD/eval_runs/dev3/haiku/plan.csv" "$PWD/eval_records/dev3/haiku" \
    --out "$G/sheets" --blind "$K/key.csv" --h-path "$Q"
vestigium eval evidence-images --h-path "$Q" --out "$G/img"          # only visual questions have any
vestigium eval grading-queue "$G/sheets" --fulltext "$W/w" --images "$G/img" --seed 3 --out "$G/queue.json"
vestigium eval grader-prompt "$G/queue.json" 1                        # the prompt for grader 1
```

(For a sequential stage, the plan is `eval_runs/dev3/plan.csv` and the records `eval_records/dev3`.)

- Each sheet holds one answer under a random code, with the question, the answer points, the reference answer and the notes, plus automatic hints (key strings found, page citations).
- Give each queue entry to a fresh grading agent with the printed prompt. We used Claude Opus in Claude Code, one agent per question, at most four at a time. The grader sees all answers to its question on separate sheets in random order, without knowing which setup wrote which; it may read only the sheets, the documents' whole text and, for visual questions, the evidence screenshots; it works alone.
- Save each reply as JSON and write it into the sheets:

  ```bash
  vestigium eval fill "$G/sheets" reply_01.json --expect <the entry's codes, comma-separated>
  ```

- Check each grader's session log: it should have opened nothing but its listed files.
- Grade at least 20% of the questions again with fresh graders, on fresh copies of the sheets, to measure agreement (`compare --regrade` reports it).

## 6. Results

```bash
vestigium eval summarize "$G"/sheets/*.csv --key "$K/key.csv" --out summary.md
vestigium eval compare "$G"/sheets/*.csv --key "$K/key.csv" --plan "$PWD/eval_runs/dev3/haiku/plan.csv" \
    --records "$PWD/eval_records/dev3/haiku" --h-path "$Q" --fulltext-index "$W/w/index.csv" --out compare.md
vestigium eval costs "$PWD/eval_runs/dev3/haiku/plan.csv" "$PWD/eval_records/dev3/haiku"
```

- `summarize`: strict and lenient accuracy per setup, fully correct questions, fabrications, unanswerable questions.
- `compare`: question-by-question differences (T − P, T − W) with a bootstrap interval and a sign test; cost and time ratios; breakdowns by document class, question type, documents too large for a 200k context, and scans.
- `costs`: tokens, tool calls, images, host seconds and the list price Claude Code reports, per setup.

Expect your numbers to differ from the development numbers on the project's Results page (its release check used `old57` and the 8 single-document questions of `dev3`; its figure check used `vis_dev`): two runs of the same question differed in cost by a median of 13–23% in our tests, other Claude Code versions behave differently, and these development questions were seen during development.

## 7. How the formal confirmation was run

The confirmation used the same tools, with these additions, all fixed in a pre-registration committed before the first run:

- **New questions.** 29 text questions and 18 visual questions written by a separate agent session, never used in development, kept private. A rule fixed in advance added 4 more after the first 15 were graded.
- **Questions answered.** Only four: cost and time, T − P accuracy (Haiku), whether T is not worse than W′ within 5 percentage points (Haiku; the main question), and fabrications.
- **Setups.** Haiku ran T, P and W′ twice (the two W′ passes were averaged) on the text questions, and T, P and V′ (repaired text, same folding) on the visual ones; Opus (effort `xhigh`) ran T and W′ on the text questions. Three lanes as above; held-out directories and a separate Claude config directory per lane kept the private questions out of the usual logs.
- **Grading.** One blind Opus grader per question with every answer to it; a second, fresh grader for every question where the compared setups scored differently plus a random share of the others (at least 20% in all); a third agent for points where the two disagreed, seeing only the question, the point, the answer and the evidence pages. The main analysis uses the first grading; the settled one is a sensitivity check.
- **Analysis** (`tglw.nextround.stats`; the import package `tglw` keeps the project's early name):
  - per question, d = strict score of T minus the mean of the two W′ passes;
  - non-inferiority holds if the one-sided 97.5% lower bound of mean(d) from 10,000 resamples of questions (`bootstrap_lower`) is above −5 points; the exact sign-flip test (`exact_signflip`) gives a p-value for reference;
  - T − P the same way, with text and visual questions resampled as separate strata;
  - cost and time: ratio of totals over the same questions, with an interval from resampling whole documents (`ratio_ci_by_document`);
  - the number of questions was re-estimated once from the direction-blind mean of d² after 15 questions (`needed_questions`).
- **Checks.** Every run audited; no run read another run's records or the answers. After the report, a separate agent recomputed the main numbers with its own code and got the same results.

```python
from tglw.nextround import stats          # tglw: the import package, named after the project's early name
d = [...]                                   # per-question T − W′, strict scores in [-1, 1]
lower = stats.bootstrap_lower(d, seed=20261104)
p, method = stats.exact_signflip(d)
print(f"mean {sum(d) / len(d):+.3f}, lower bound {lower:+.3f}, p {p:.3f} ({method})")
```

The results and their limits are on the project's Results and Methods pages.

## 8. Your own questions and settings

- Write questions as JSONL in the format of `questions/*.jsonl` (see the README); `split` is `dev`. Every `doc` must be a name in your Vestigium library.
- Keep questions you want to stay unseen in a directory whose name starts with `heldout`. `vestigium eval` then refuses to read them unless you pass `--allow-heldout`, and a stage or records directory under such a name must keep both under it and use a Claude config directory named `.claude-heldout*`, so their prompts and logs stay out of your usual `~/.claude`.
- Settings, by environment variable:

| Variable | Default | What |
|---|---|---|
| `TGLW_EVAL_CLAUDE` | `claude` | The Claude Code command |
| `TGLW_EVAL_PYTHON` | the Python running `vestigium eval` | The interpreter that starts setup T's MCP server |
| `TGLW_EVAL_GUARD` | none | Comma-separated path fragments; a run's tool call naming one is flagged (for example where your answer keys live) |
| `TGLW_EVAL_HELDOUT` | `heldout` | Comma-separated directory-name prefixes that mark held-out material |

- `vestigium eval <command> --help` describes every option.
