# Results

[中文](RESULTS_ZH.md) · [Methods](METHODS.md) · [README](../README.md)

This page reports one pre-registered comparison, run in October 2026. How it was set up and kept fair is on the [Methods](METHODS.md) page. All accuracy numbers come from an AI judge; a human spot check is still to be done.

## The test in brief

| | |
|---|---|
| Agent app | Claude Code 2.1.283, run headless, a fresh session for every question |
| Models | Claude Haiku 4.5 (`claude-haiku-4-5-20251001`); Claude Opus 5.5 (`claude-opus-5-5`, reasoning effort `xhigh`) |
| Documents | Drawn from 59 public documents in English (the Hong Kong filings also contain some Chinese) of six kinds: arXiv papers, Apple financial filings and reports, US GAO reports, Hong Kong Stock Exchange filings, NASA technical reports (some scanned), NIST publications. 3 to 525 pages each |
| Questions | 29 hard text questions (6 need two documents; 2 have no answer in the documents), 4 more added by a pre-registered rule, and 18 questions that need a figure (2 with no answer). Written for this test, never used in development, not published. 3 of the text questions overlap with older development questions; see [Methods](METHODS.md#new-questions) |
| Runs | 240, all valid on the first attempt |
| Grading | Blind, by Claude Opus agents; at least 20% graded again by fresh agents; disagreements settled by a third agent |

### Four ways of reading

| Name | The model gets | Tools it may use |
|---|---|---|
| **T** | Vestigium | Only the Vestigium MCP tools |
| **W′** | The document's full text as extracted by Docling (the parser Vestigium also uses), as one plain text file, with long lines split after sentence ends (at most about 400 characters) | Claude Code's Read, Grep, Glob |
| **P** | The PDF file | Read, Grep, Glob; Read shows the chosen PDF pages as images |
| **V′** | Like W′, but with the repaired text that Vestigium serves | Read, Grep, Glob (run only on the figure questions) |

W′ is the main baseline: "give the model the whole text and let the agent app search it". Why its lines are folded is explained in [Methods](METHODS.md#a-fair-text-baseline).

## Main results (Claude Haiku 4.5)

Accuracy is the share of required answer points the judge marked correct, averaged over questions. Differences are in percentage points. Intervals are 95% bootstrap intervals; with this few questions, such intervals tend to be a little too narrow (see [Methods](METHODS.md#analysis)).

### Accuracy against the whole text (the main question)

| Comparison | Questions | T − W′ | 95% interval | Reading |
|---|---|---|---|---|
| **Main analysis** | 31 | **+1.2** | [−1.8, +4.4] | **Not worse**: the lower bound is above −5 |
| Only the 27 original questions | 27 | +0.5 | [−2.6, +4.1] | Sensitivity check |
| Using the settled (third-agent) grades | 31 | +2.6 | [−0.9, +6.7] | Sensitivity check |
| Only the 6 two-document questions | 6 | −2.5 | [−8.2, +1.9] | Description only |

- The estimate is positive, but the interval includes 0. So the finding is "not worse by more than 5 points". It is **not** "more accurate".
- Before the runs, a reviewer predicted that T would lose by at least 3 points on questions that ask for long lists. It did not: T − W′ = +3.2 [−1.9, +10.6] on those questions.

### Accuracy against the PDF

- T − P = **+21.2 points** [+12.3, +30.8], p < 0.001 (43 questions).
- On the text questions alone the gap is +30.1. But W′ − P is +29.6 there as well. So most of this gain comes from reading text instead of page images. It is not all due to Vestigium.
- On the figure questions alone the gap is +6.2 [−10.4, +25.0], which is not significant.

### Cost and time

Each ratio is the total for T divided by the total for the other setup, on the same questions. Cost is the list price that Claude Code reports. Time is wall-clock time on the machine that ran Claude Code. Intervals resample whole documents.

| Model | Comparison | Cost ratio | Time ratio |
|---|---|---|---|
| Haiku | T / W′ | **0.51** [0.44, 0.58] | **0.54** [0.47, 0.63] |
| Haiku | T / P | **0.25** [0.19, 0.33] | 0.60 [0.52, 0.72] |
| Opus | T / W′ | 0.80 [0.73, 0.89] | 0.87 [0.78, 0.95] |

- With Haiku, T cost about half as much as W′ and took about half the time. It cost about a quarter of P.
- With Opus, T saved about one fifth.

### Made-up facts

Only P with Haiku produced answer points the judge marked as made up: 6 points. T and W′ had none.

### Accuracy of each setup (description only)

| Questions | T, Haiku | W′, Haiku (two runs) | P, Haiku | V′, Haiku | T, Opus | W′, Opus |
|---|---|---|---|---|---|---|
| 27 hard text questions | 91.8% | 91.2% / 91.4% | 61.7% | — | 100% | 99.6% |
| 16 figure questions | 32.8% | — | 26.6% | 3.1% | — | — |

- With Opus, T and W′ are both at the ceiling on these questions, so the test cannot rank them.
- Haiku is weak at figures, even with Vestigium. V′, which has text only, scored 3%, which shows these questions really need the figure.
- Opus was not run on the figure questions. That part was dropped before the runs to stay within budget.

## How sure are we

- **Grading was stable.** Of 960 graded answer points checked by a second grader, the second grade was higher 6 times and lower 6 times. Among the 25 re-graded questions that decide the T − W′ result, the second grade favoured T on 5 and W′ on none. If the first grades were biased, they were biased against T.
- **Independent recomputation.** A separate agent wrote its own analysis code from scratch, without reusing ours. It got the same main numbers: T − W′ = +1.18, T − P = +21.21, Haiku cost ratio T / W′ = 0.507. Intervals differed only by resampling noise.
- **Against earlier estimates.** Before the runs, the development estimates for T − W′ ranged from −1.8 to +0.6 points, and a reviewer predicted −1.5 to −0.5. The confirmed value, +1.2, is above both, but its interval still includes 0, so it is not evidence that Vestigium is more accurate.
- **Why 31 questions were enough.** T and W′ mostly got the same questions right and wrong, so the per-question difference varied little (standard deviation 0.098). That gave about 81% power to show non-inferiority.

## What this does not show

- **More accurate than the whole text.** Not shown; only "not worse by more than 5 points".
- **Grading by humans.** The judge is from the same model family as the answering models. A human spot check is planned.
- **Other agent apps and models.** The main test used Claude Code with Claude models. Copilot and Cursor were not tested. A GPT model used the tools without errors on 5 questions (below), which shows they work, not how accurate it is.
- **Other languages and fields.** The main test used English public reports. Chinese had only a small trial on easy questions (below); medical and legal documents were not tested.
- **Figures.** Small models remain weak; Opus was not run on figure questions.
- **Per-request billing.** If your plan charges per request rather than per token, saving tokens may not save money. We have not checked this.

## 0.1.0 and the tested version

The numbers above are for the version we tested in October 2026. 0.1.0 adds the features described in [Tools](TOOLS.md), some of them switched on by default. Before release we checked that it does not do worse:

- **How:** 65 development questions, Claude Haiku 4.5, one run each, 0.1.0 paired with the tested version; graded blind by an AI judge, with five no-regression rules fixed before the runs.
- **Result:** mean strict accuracy 97.2% for 0.1.0 against 97.1% for the tested version; cost 0.92 of the tested version; all five rules passed.
- **Reading:** no regression. The difference is too small to call 0.1.0 better, and development questions are easier than the confirmation questions.
- **Not kept:** a hint asking the model to answer item by item scored 1.5 points lower on 21 questions, so it stays off.
- **Figures:** choosing the picture scale from the smallest text did no worse than the fixed scale on 15 development questions that need a figure, so it is on by default. A note giving the token cost of pictures did not help and stays off. Haiku remains weak at figures.

## Chinese (experimental)

After the main test, we ran a small trial on Chinese PDFs to decide whether to support Chinese at all. Three rules were fixed before the runs: Vestigium at most 5 points below the whole Chinese text, no made-up facts, and no higher cost. All three were met, so Chinese is supported as experimental.

- **Questions:** 15 Chinese development questions (14 answerable) on Hong Kong exchange circulars, a regulation, a listed company's annual report, central-bank documents and a scanned document. Claude Haiku 4.5, one run each, graded blind by an AI judge.

| Setup | Strict accuracy | Fully correct (of 14) | Total cost | Total time |
|---|---|---|---|---|
| Vestigium | 96.0% | 11 | $0.54 | 392 s |
| Whole Chinese text with the app's search, folded like W′ | 96.5% | 11 | $1.03 | 654 s |
| The same, with cleaned text | 98.8% | 13 | $0.97 | 542 s |

- All three rules were met: Vestigium scored 0.5 points below the whole text (the bound was 5), made up no facts, and cost about half as much (0.52), in 28–40% less time. So Chinese is supported as experimental. Vestigium was not shown to be more accurate.
- No setup made up facts, and all three correctly said that the unanswerable question has no answer in the report.
- **Limits:** only 15 questions, each run once; all three setups scored above 96%, so these questions are too easy to show differences on hard ones. OCR on scanned pages can turn some traditional characters into simplified ones.

## Without MCP: the file export

For apps that cannot use MCP, `vestigium export` writes a document as plain text files (see [Agent apps](CLIENTS.md#apps-without-mcp)). We checked it on 10 development questions (8 answerable) with Claude Haiku 4.5, one run each, graded blind together with the other two setups:

| Setup | Strict accuracy | Fully correct (of 8) | Total cost | Total time |
|---|---|---|---|---|
| File export | 97.9% | 7 | $0.83 | 565 s |
| Whole cleaned text (V′) | 91.6% | 5 | $1.38 | 809 s |
| Vestigium over MCP | 95.8% | 7 | $0.43 | 263 s |

- The rule fixed in advance was that the export must not score more than 4 points below the whole text. It passed, so where MCP is not available we recommend the export over a single whole-text file. It also cost about 0.6 of the whole text.
- Where MCP is available, use Vestigium over MCP: the export cost about twice as much and took about twice as long.
- All three setups correctly declined the 2 unanswerable questions, and none made up facts.
- With 8 answerable questions run once each, we do not quote the accuracy differences as effect sizes.

## Other models (experimental)

A GPT model used the same tools on 5 development questions without errors. This shows the tools work with a non-Claude model, not how accurate it is. Other models remain experimental.

## Where the savings come from

The cost of an agent run in Claude Code is mostly the fixed prompt of the agent app, the model's own output, and what the tools deliver. With the whole text, the agent has to find and read the right lines itself with the app's search and read tools; with the PDF, it looks at page images, which cost more tokens than text. Vestigium returns ranked passages within a size limit, and images only when the agent asks for a picture of a figure or table.

## Reproducing

How to rerun the comparison on the development questions provided with the release, and how the confirmation was analysed: [Methods](METHODS.md#reproducing).
