# Methods

[中文](METHODS_ZH.md) · [Results](RESULTS.md) · [README](../README.md)

This page explains how the comparison on the [Results](RESULTS.md) page was set up, how we tried to keep it fair, and how you can rerun it.

## What we compared

Each question was answered in a fresh Claude Code session (version 2.1.283, headless). The prompt was the same for every setup except for one line that names the material:

| Name | Material | Tools |
|---|---|---|
| **T** | Vestigium, limited to the documents of that question | Only the Vestigium MCP tools |
| **W′** | The full text extracted by Docling, as one text file, with long lines split after sentence ends | Read, Grep, Glob |
| **P** | The PDF file | Read, Grep, Glob (Read shows PDF pages as images) |
| **V′** | The repaired text that Vestigium serves, folded the same way | Read, Grep, Glob; only on figure questions |

A run that took more than 900 seconds or 60 turns was stopped and graded on whatever answer it had.

Models: Claude Haiku 4.5 ran T, W′ (twice), P and V′. Claude Opus 5.5 (reasoning effort `xhigh`) ran T and W′ on the text questions. Opus was not run with P: in a pre-check it already scored 100% with both T and W′. Opus was not run on figure questions: that part was dropped before the runs to stay within the budget.

## A fair text baseline

The obvious alternative to Vestigium is to give the agent the whole text and let it search. We found that our first version of this baseline was handicapped:

- Claude Code's Grep hides any matching line longer than 500 characters ("Omitted long line").
- Our text files had lines of up to about 1,000 characters, so the agent often could not see the lines it had found.

So we built W′: the same text, with long lines split after sentence ends so that no line is longer than 400 characters (up to 404 counting the spaces kept at a cut); a single sentence longer than that is split between words. This is well under Grep's 500. Not a single character is dropped or changed; removing the added line breaks gives back the original text exactly. In all confirmation runs, Grep hid **0** lines.

We no longer quote our earlier development numbers, which were measured against the unfolded text. All numbers on the [Results](RESULTS.md) page are against W′.

W′ still has one cost of its own: large Grep results are moved to a file by Claude Code (22–28 times across the Haiku W′ runs). That is a limit of the agent app, and it is part of what "whole text plus the app's own search" means in practice.

T and W′ do not read exactly the same text: T serves a repaired version (lost table text restored, misplaced OCR copies hidden). V′ uses the repaired text in the W′ format.

## New questions

- **Who wrote them.** A separate agent session wrote the questions. It took no part in developing Vestigium, and it did not see the results.
- **How they were checked.** Each question was answered blind and checked against its answer key and evidence pages. A reviewer then looked for ambiguity and wrong keys, and a script checked the format: 0 errors. In the batch that supplied the 4 added questions, some were reworded after review and not answered blind again.
- **How hard.** The 29 text questions have about 9 required answer points each, against about 6 in our older questions. About three quarters need two or more steps, 10 ask for lists of 8 or more items, and 6 need two documents.
- **Kept private.** The questions were never used during development and are not published. Having served this confirmation, they will not be reused as unseen questions; later confirmations will use a separate unpublished set, written to avoid the facts our development questions test. Our development questions are provided with the release instead; see [Reproducing](#reproducing).
- **Overlap with older questions.** Of the 29 text questions, 2 test the same facts as older development questions and 1 uses the same kind of trap. They were flagged before the runs and kept in the main analysis; dropping them was a planned sensitivity check.
- **Figure questions.** The 18 figure questions were written the same way, with evidence screenshots for the grader.

## Pre-registration

Before any confirmation run, the full plan was written down and committed to version control. It fixed:

- **The four questions to answer**, and only these:
  1. cost and time;
  2. accuracy of T against P with Haiku;
  3. whether T is not worse than W′ with Haiku, within **5 percentage points** (the main question);
  4. made-up facts.
- **The statistics**: exact tests, intervals, random seeds, and the exact wording of each possible conclusion. "Tied" or "equivalent" were not allowed as wordings.
- **The questions**: file checksums, which questions run first, and the rule for adding questions.
- **The grading rules**: how many answers are graded again, and how disagreements are settled.
- **The run rules**: what counts as a failed or invalid run, and when the whole batch stops.
- **A budget cap** of $120 at list price.

Every reading of the plan and every change was logged with its reason: 14 interpretations settled before the runs and 4 changes after they began. None changes a conclusion. The full pre-registration document is not published, because it identifies the private confirmation questions; its decision rules are summarised on this page. A reviewer also wrote down predictions before the runs, including one against Vestigium: that it would lose by 3 points or more on long-list questions. That prediction failed.

## Running

- **Batch.** Three lanes ran at the same time, starting runs 60 seconds apart. Each lane had its own run directory and Claude Code configuration, and every run was a fresh session.
- **Stops.** Any rate-limit, access or API error would stop all three lanes. None happened.
- **Audit.** Every run's tool calls were checked: T could use only Vestigium tools, and the others only Read, Grep and Glob. A run that read the answer key or other runs' records would have been scored as all wrong. There were none.
- **Allowlist.** Vestigium was started with an allowlist of only the question's documents, so T could not open other documents.
- **Totals.** 228 planned runs plus 12 added runs: all 240 were valid on the first attempt.

## Grading

- **Blind.** Each question got one Claude Opus grading agent. All answers to that question went to the same grader, each on its own sheet under a random code, in random order, with nothing saying which model or setup wrote them. The grader could read the grading sheets and the full text of the question's documents (plus the evidence screenshots for figure questions). It could not start helpers of its own.
- **Graded again.** At least 20% of the answers were graded again by fresh agents. For Haiku, this covered every question where the setups being compared scored differently, plus a random sample of the others. For Opus, it was a random 20% of the questions.
- **Disagreements.** Points where the two grades differed went to a third agent. It saw only the question, the point, the answer and the evidence pages.
- **Scores.** Strict: the share of required points marked correct. Lenient: partly correct also counts. The main analysis uses strict scores from the first grading; the settled grades are a sensitivity check.
- **A check on shared grading.** Giving one grader the answers of both models at once could have biased it. A pre-set test compared first and second grades; it did not trigger.

Graders are from the same model family as the answering models. A human spot check of the deciding points is planned.

## Analysis

- **Main question (T − W′).** The mean per-question difference in strict score, with W′ averaged over its two runs. Non-inferiority holds if the one-sided 97.5% lower bound from 10,000 bootstrap resamples of questions is above −5 points. An exact sign-flip permutation test gives the p-value for reference.
- **T − P.** The same per-question difference, with text and figure questions resampled as separate strata. Significance comes from the exact test.
- **Cost and time.** The total for T divided by the total for the other setup, on the same questions. Intervals resample whole documents, because questions on the same document are not independent.
- **How many questions.** After the first 15 questions were graded, the spread of the differences (not their direction) was used to compute how many questions were needed. That rule added 4 questions.
- **Interval accuracy.** In our simulations, two-sided bootstrap "95%" intervals on about 30 questions covered the truth about 90% of the time, mostly missing on the upper side. The one-sided lower bound used for the decision covered 96–97.5%.

The analysis code was written, tested and committed before the runs. Afterwards, a separate agent recomputed the main numbers with its own code and got the same results.

## Cost of the test

The whole confirmation cost $95.68 at list price: $16.92 for the pre-checks, $34.88 for the 240 runs and $43.88 for grading (112 grading agents). The independent recomputation cost $1.16 more. These are list prices that Claude Code reports, not what any particular plan bills.

## Reproducing

The confirmation questions stay private, so the confirmation itself cannot be rerun. Everything else can: the release comes with the **Vestigium public development questions**, 122 questions about 30 public documents, in five sets:

| Set | Questions |
|---|---|
| `old57` | 57 test questions from an earlier comparison, run before the one on the [Results](RESULTS.md) page and since released for development. The numbers of that earlier comparison are not on the Results page; the release check described there reused these questions |
| `dev`, `dev2`, `dev3` | Text questions written during development |
| `vis_dev` | Questions that need a figure |

The package is in the [`benchmark/`](../benchmark/README.md) folder of the repository. It also holds a download list (the public source and SHA-256 of each PDF; we do not redistribute the PDFs) and a step-by-step guide, [`REPRODUCE.md`](../benchmark/REPRODUCE.md), with every command.

In short:

1. **Install** `pip install "vestigium[ingest,eval] @ git+https://github.com/Yonagi043/vestigium"`. You also need Claude Code (we used 2.1.283) and poppler-utils (`pdftoppm`, `pdfimages`).
2. **Fetch and process the documents**: `vestigium eval fetch-docs` downloads them and checks their checksums, then a generated script runs `vestigium ingest` on each one under the names the questions use.
3. **Build the materials**: `vestigium eval fulltext` writes the whole text, `vestigium eval fold` turns it into W′, and `vestigium eval snapshot` freezes Vestigium for setup T.
4. **Run**: `vestigium eval plan`, then `stage` and `headless` one run at a time, or `stage-parallel` and `parallel` in three lanes as in the confirmation.
5. **Grade and summarise**: `vestigium eval sheets --blind KEYFILE` writes the grading sheets under random codes and keeps the key in a separate file; `grading-queue` and `grader-prompt` give each question to a fresh grading agent; `fill` records its grades; `summarize`, `compare` and `costs` produce the tables.

During evaluation the server is registered under the name `tgl`, as in our runs, so the tool names the model sees match ours. Every run calls a model through your own Claude Code. One question in one setup cost us from a few cents (Haiku with Vestigium) to about 30 cents (Haiku reading the PDF), plus grading.

Expect differences:

- These questions are easier than the confirmation questions, and we saw them during development.
- Two runs of the same question differed in cost by a median of 13–23% in our tests.
- Other Claude Code and Docling versions behave differently.
- A few public PDFs have changed since we downloaded them; the download list says which could not be matched.
