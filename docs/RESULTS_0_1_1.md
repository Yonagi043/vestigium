# Results: 0.1.1

[中文](RESULTS_0_1_1_ZH.md) · [0.1.0 results](RESULTS.md) · [Rules](RULES.md) · [README](../README.md)

Version 0.1.1 adds [rules](RULES.md) that the tools carry out themselves, and a [record of each reading](TRACES.md). With its release defaults, one rule is on: when a section continues on the next page, read that page too. The `trace` and `rules` tools are off until you turn them on.

Results vary from run to run, so each number comes with its range. None of the differences below is large enough to call 0.1.1 more accurate, cheaper or faster than 0.1.0.

## The 0.1.1 release defaults, on development questions

24 development questions (7 hard; 12 controls, 4 of them with no answer; 5 that need a figure), 6 runs per question for each setup. Cost and time are measured on the 19 hard and control questions, as fixed before the runs; the 5 figure questions are reported separately. Time is wall-clock time, as in the [0.1.0 results](RESULTS.md); model time is the time the model took, as Claude Code reports it (`duration_api_ms`). On the hard questions, lost points are counted per pass: one pass runs each of the 7 hard questions once, and the points lost are added up; the table gives the average over the 6 passes. All setups ran side by side at the same time and were graded together, blind, by an AI judge. Model: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) in Claude Code 2.1.283.

| | 0.1.0 | 0.1.1 release defaults (tools off) | 0.1.1 with the two tools on |
|---|---|---|---|
| Cost (÷ 0.1.0), hard and control questions | | **0.967** (0.892 to 1.053): 3.3% less on average, within the variation | 1.059 (0.949 to 1.166) |
| Time (÷ 0.1.0), hard and control questions | | **0.947** (0.832 to 1.065): about 5% less on average, within the variation; model time 0.945 (0.822 to 1.071) | 1.017 (0.894 to 1.133); model time 1.013 (0.877 to 1.143) |
| Hard questions: lost points per pass | 8.33 | **8.00** (difference −3.67 to +3.00) | 10.67 (difference −2.50 to +8.17; mostly one question, looks like variation) |
| Hard questions: lost points from stopping too early, per pass | 1.00 | **0.50** | 0.50 |
| Control questions: score | 98.2% | **100%** | 99.5% |
| Questions with no answer: declined | 24 of 24 | 24 of 24 | 24 of 24 |
| Figure questions: strict accuracy (reported only) | 58.3% | **58.3%** | 60.4% |

- **The two tools cost a little on every turn.** With them on, each turn carries their descriptions, about 350 tokens. In the 144 runs with the tools on, the model never called them while reading. Turn them on when you want to look at traces or write rules.
- **0.1.0 itself varies between tests.** In an earlier test it lost 9.67 points per pass on the hard questions, 2.17 of them from stopping too early, against 8.33 and 1.00 here.

## Confirmation on 59 new questions

The 59-question confirmation was run once, on the 0.1.1 test configuration. That configuration had the same rule plus two more: evidence when a search finds nothing, and a summary after many repeated searches. It also had the two tools on.

| | 0.1.1 test configuration against 0.1.0 | 95% interval |
|---|---|---|
| Accuracy | **+1.08 points** | −0.40 to +2.68: not worse (the bound was 3 points), not shown to be more accurate |
| Cost (÷ 0.1.0) | **0.988** | 0.928 to 1.050: met the cost rule fixed in advance (ratio at most 1.00); the interval includes 1 |
| Time (÷ 0.1.0) | **0.99** | 0.94 to 1.04: no difference seen |

Lost points from stopping too early fell from 2 to 0 across all runs.

These questions are used for one confirmation only, so the 0.1.1 release defaults were not tested on them again. The release defaults are closer to 0.1.0 than the test configuration was, but that they are also not worse on these questions is an inference, not a measurement.

## Stronger model (spot check, Claude Opus)

19 questions, 2 runs each per setup, with `claude-opus-5-5`: almost all correct in every setup, and the controls and the questions with no answer were all correct.

| Setup | Cost (÷ 0.1.0) |
|---|---|
| Two tools off | **1.022** (0.965 to 1.075) |
| Two tools on | 1.044 (0.975 to 1.110) |

Both intervals include 1, so a higher cost was not shown. Time was 1.07 in both: 1.07 (0.99 to 1.16) with the tools off, 1.07 (0.95 to 1.23) with them on.

## Known weak spots

- **Stopping early.** The target fixed in advance was to cut lost points from stopping too early by two thirds. It was not met in this test: those points fell from 6 to 3, only half, and the numbers are too small to show such a cut.
- **Read but not written.** This is the model's part; the tools cannot fix it.
- **Writing rules.** Haiku wrote rules correctly 60% of the time, Opus 20 of 20. Use a stronger model to set rules.

## Other checks

- **Small language.** The two new tools' descriptions total about 300 tokens by our token counter; 54 wrongly written rules were all refused, each with a fix.
- **Agents read traces.** Haiku 88%, Opus 100%.
- **No regression.** With all rules off, replies are byte-for-byte those of 0.1.0 on our test documents.
- **Boundaries.** Traces and rules stay on the machine (0 network connections when tested offline); undo, factory reset and all-off work.

## What this does not show

- That 0.1.1 is more accurate, cheaper or faster than 0.1.0: the differences are within the variation.
- That the 0.1.1 release defaults passed the 59-question confirmation themselves; that was done on the test configuration.
- What seeing traces and writing rules are worth in practice: the tools are off by default, and their value has not been measured yet.
- Apps other than Claude Code, or models other than Claude.
- The effect of rules you write yourself.
- That the AI judge agrees with people: no human spot check was done for 0.1.1.
