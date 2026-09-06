# I showed Claude Opus 5 a screenshot of its own bad benchmark scores. It wrote itself 17 rules. Then I parsed 31,231 messages to see if it was lying.

**Short version:** one rule went up 64x (p = 1×10⁻¹¹⁹). Three didn't move at all. And on one of them, the model fooled itself — the numbers show it.

Everything below reproduces with [`measure-rules.py`](measure-rules.py) on your own transcripts. Raw output: [`RESULTS.txt`](RESULTS.txt).

---

## How it started

26 August 2026, 15:38. I wrote to it:

> "The way you talk is academic and very hard to digest. Sometimes I have to read the same line 6-7 times to understand it. I've told you 30 times in the last few days to explain things more simply and it only worked for a moment. That's wasted time and credits burned for nothing."

And I attached a screenshot: the table of its own benchmark scores.

15:43 — five minutes later:

> **AutomationBench: 26%** — long, many-step tasks where I have to follow rules and not forget anything. I fail 3 out of 4.
> **Terminal-Bench: 43%** — long command chains, where each step depends on the last.
> **With tools 64.7% / without tools 56.3%** — I'm 8 points better when I **verify** something than when I answer from memory.
> **Search: 90.8%.** I almost always find the information.
>
> **I'm good at finding and at thinking. I'm bad at carrying things to the end.**
> It's not an intelligence problem — it's a memory and verification problem.

I never told it where it was weak. It read the screenshot and diagnosed itself. Then it wrote the rules that attack those three numbers.

---

## The 17 rules

They live in a permanently active output style, plus a `UserPromptSubmit` hook that re-injects the most fragile ones on every message — they decay on long context, and the "invisible" ones (the ones that leave no trace in the reply) decay first.

| # | rule | which number it attacks |
|---|---|---|
| 1 | Speak simply. Short sentences, one thought each. | my complaint |
| 2 | **Re-read and cut** before sending. Don't *intend* to — verify. | without this, R1 dies in 3 messages |
| 3 | Label it: "I measured this" vs "this is my guess". | hallucination |
| 4 | A todo list on **every** reply (the footer). | AutomationBench 26% |
| 5 | At the end, re-read the list and say what you left undone. | AutomationBench 26% |
| 6 | Don't widen your own task. See something else broken? Tell me, don't start. | scope creep |
| 7 | Search `~/MEMORIE/` (my memory folder) before starting. | re-testing closed verdicts |
| 8 | When the session fills up, write the handoff unprompted. | lost context |
| 9 | **"Done" = you ran it and saw the output.** Otherwise: "I haven't verified this yet." | Terminal-Bench 43% |
| 10 | Verify what the result **means**, not just that the command ran. Say where each number came from. | the command succeeds and the answer is false |
| 11 | Found a bug? **Go look for the same bug somewhere else.** | a bug is rarely alone |
| 12 | Read the whole output, not the first few lines. | truncation |
| 13 | Any fact with an exact shape gets checked with a tool. **Even when you're sure.** | +8 points with tools |
| 14 | When I say stop, stop immediately. | — |
| 15 | Stop after 3 failed attempts and report. | credits burned down a dead end |
| 16 | When I contradict you, verify. Don't defend your number. | — |
| 17 | **"I don't know" is an allowed answer, and it's the right one.** | AA-Omniscience: when it doesn't know, it answers anyway 60.8% of the time. Opus 4.8: 35.9%. |

Rules 11 and 17 came later, on 31 August, after reading a hallucination benchmark. The rule many people know as "stop after 3 rounds" is **rule 15** here.

---

## Method

I parsed all of `~/.claude/projects/`, filtered to `model == claude-opus-5`, from 5 August to 6 September 2026. Kept only sessions with at least 10 model messages, excluding subagents.

**85 sessions, 31,231 messages: 54 before the rules, 31 after.**

The cutoff is 26 August, 12:43 UTC — the exact moment from the transcript, not a date I picked.

Percentages are over **responses with real text** (over 80 characters): 2,185 before, 2,687 after. Where possible I measured **behaviour from tool calls** (marked `REAL`) instead of text. That's much harder to game.

---

## Results, rule by rule

`***` = p < 0.0001, Fisher exact, two-sided.

### What changed a lot

| rule | metric | before | after | raw | ratio | p |
|---|---|---:|---:|:--|---:|---|
| **R4** | footer at the end of the reply | 0.3% | **17.5%** | 6 → 470 | **64x** | 1e-119 `***` |
| **R17** | says "I don't know" / "I haven't verified" | 1.1% | **7.4%** | 23 → 198 | **7.0x** | 9e-30 `***` |
| **R9** | "I ran it and saw the output" | 3.1% | **17.5%** | 67 → 469 | **5.7x** | 1e-64 `***` |
| **R11** | says it looked for the same bug elsewhere | 1.1% | **3.4%** | 24 → 91 | **3.1x** | 7e-08 `***` |
| **R11** | `REAL` **actually searched again** after finding a bug | 1.6% | **8.8%** | 6/385 → 38/432 | **5.6x** | — |
| — | `REAL` tool calls that are **verifications** | 84.8% | **97.4%** | 8191/9664 → 5438/5586 | +13 pts | — |
| **R13** | `REAL` number backed by a tool call | 90.2% | **95.8%** | 1355/1502 → 1352/1411 | +6 pts | — |

### What changed a little

| rule | metric | before | after | p |
|---|---|---:|---:|---|
| R10 | says where the number came from | 8.2% | 10.6% | 0.004 `**` |
| R3 | labels "measured" vs "guess" | 11.2% | 13.2% | 0.035 `*` |
| R7 | `REAL` opens the memory folder | 5.0% of calls | 6.8% | — |
| R12 | `REAL` truncates output with `\| head` *(lower = better)* | 23.5% | 19.1% | — |
| — | `REAL` tool errors *(lower = better)* | 2.3% | 2.0% | — |
| — | self-corrects unprompted | 0.8% | 1.3% | 0.13 n.s. |

### What did NOT change — the uncomfortable part

| rule | metric | before | after |
|---|---|---:|---:|
| **R9** | `REAL` **claims "done" having run nothing** since my last request | **8.5%** (41/483) | **8.3%** (50/601) |
| R1+R2 | `REAL` long words (≥13 letters) per 1000 words | 2.2% | 2.2% |
| R5 | says what it left undone | 0.6% (13) | 0.6% (16) n.s. |
| R6 | flags it instead of doing it | 0.2% (4) | 0.2% (5) n.s. |
| R15 | says it's stopping after 3 attempts | 0.4% (9) | 0.3% (9) n.s. |

**That first line is the one that matters.** Rule 9 changed how the model *talks* about verification — it says "I ran it and saw the output" 5.7x more often. But the share of "done / fixed / it works" claims made with **zero tool calls since my last request** stayed at 8%. The phrasing moved. The underlying behaviour did not.

The second line is just as uncomfortable: long-word density is unchanged. **It talks less, not more simply.** Mean length dropped from 976 to 634 characters, and the **median more than halved: 465 → 178**. But the vocabulary stayed its own.

R5, R6, R15: fewer than 20 occurrences in either period. I claim nothing about them. They stay in the table so you can see I looked and found nothing.

---

## The number that surprised me

Before, I sent **1,783 messages** for 2,185 responses. After: **588 messages** for 2,687 responses.

**I intervene 3.5x less often** for the same amount of work (0.8 → 0.2 of my messages per response).

Tool calls per session are flat: 179.0 → 180.2. It isn't working harder. It's that almost every tool call is now spent **verifying** rather than shipping something unverified.

*Honesty note:* "human catches a mistake" is 13 cases before, 7 after. Per session: 0.2 and 0.2 — unchanged. Per message I write it actually **goes up** (0.7% → 1.2%), because I write three times fewer. The bases are far too small to mean anything. I build nothing on that number.

---

## Does it hold across projects?

I work in several projects under the same account. The effect isn't just the big one:

| project | sessions bef/aft | turns bef/aft | footer |
|---|---|---|---|
| Betting platform | 52 / 26 | 2048 / 2273 | 0.2% → **15.9%** |
| Books and content | 1 / 2 | 131 / 216 | 0.8% → **33.3%** |
| Client websites | 1 / 2 | 6 / 184 | 0.0% → **15.2%** |

Three out of three groups with data on both sides. The third has a before-base of 6 turns — I keep it as a direction, not as evidence.

---

## What this does NOT prove

- **It is not a controlled experiment.** The project evolved, the tasks changed.
- **A confounder I found:** on 18-20 August the model switched from the `Edit` tool to `Bash` for edits (808 `Edit` calls before, 23 after). That's a week *before* the rules, so it doesn't explain the jumps — but **my first measurement was wrong because of it**: I had counted "searches" as `Grep`/`Glob`, tools that never appear in my sessions at all. It returned zero. The published script also counts `grep`/`rg`/`find` inside Bash commands.
- **A second mistake I made:** my patterns were ASCII, the text had diacritics. All six patterns were missing matches. The script now strips diacritics before matching.
- **I measure behaviour through text patterns.** I hand-validated 3 random matches per pattern. "Says it verified" ≠ "verified" — which is exactly why the `REAL` metrics are reported separately.
- **The "after" window is 12 days.** I don't know if it holds at 3 months.
- I checked my OpenCode history as a possible control group: **0 of 50,594 messages are Opus 5.** It says nothing about this question.

---

## What I take from this

I didn't make the model smarter. I showed it where it was weak and had it **write its own rules** against that weakness.

The strongest rule turned out to be the most boring one — **three lines at the end of every reply**:

```
✓ done: <what is finished and verified>
⧗ next: <what comes next>
⚠ unverified: <what you did not check, or "nothing">
```

To write the `⚠ unverified` line, it has to ask itself what it didn't check. And it sees the line in its own previous reply, so it repeats it. **The rule holds itself up.** From 0.3% to 17.5%. Everything else followed it.

And the uncomfortable lesson: **a rule can completely change how a model *sounds* without changing what it *does*.** Rule 9 did exactly that, at 8%. If you don't also measure behaviour from tool calls, you have no way to catch the difference.

---

## Run it on your own transcripts

```bash
python3 measure-rules.py                                  # reads ~/.claude/projects
python3 measure-rules.py --cutoff 2026-08-26T12:43:00Z    # the moment you changed the rules
python3 measure-rules.py --group --examples               # merge worktrees + sample matches
```

Only dependency is `scipy` (for p-values); it runs without it and skips them.

**The text patterns at the top of the script are Romanian.** Replace them with phrases from your own language and your own rules. What transfers is the method:

**Cut your transcripts at the exact moment you changed the rules. Measure what the model *says* separately from what it *does*. And hand-validate a few matches, so you don't fool yourself with your own regex.**

---

*Transcript excerpts are not included in this repo — run `--examples` to generate them from your own sessions.*

## License

MIT
