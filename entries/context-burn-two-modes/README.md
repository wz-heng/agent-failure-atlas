# Two ways a long-context agent session burns money, neither of them thinking

> **Evidence level: `trace replay`** — for the billing reconstruction.
> [`repro.py`](repro.py) replays a real per-turn usage ledger
> ([`evidence/`](evidence/)) and rebuilds both burn modes from it, including
> the decomposition that shows 90.8% of one session's cost was moving context
> around rather than generating anything.
>
> **Evidence level: `mechanism simulation`** — for the counterfactual.
> [`simulate.py`](simulate.py) uses a fake clock and a fake TTL cache to run
> the arm the ledger cannot contain: *the same task sequence under different
> session hygiene*. Its numbers are invented and are never mixed with the
> measured ones. The two live in separate files for that reason.
>
> The 2026-07-09 and 2026-07-15 diagnoses themselves are **operator
> testimony**. Where the ledger disagrees with the notes, the ledger wins and
> [§3](#3-evidence) says so — it disagreed four times.
>
> **All money figures are provider-reported list-price equivalents, not
> charges.** No billing statement was ever consulted. [§3](#3-evidence)
> explains why the distinction is load-bearing rather than pedantic.

| | |
|---|---|
| **Hit in** | Owlery — a personal agent platform driving the Claude Code and Codex CLIs (private repository) |
| **Systems** | Claude Code CLI against `Claude-opus-4-8` and `claude-fable-5`, 1M-token context window; Codex CLI for the contrast trace |
| **Platform** | macOS 26.3 (Darwin 25.3.0), Python 3.12 |
| **Incidents** | 2026-07-09 and 2026-07-15, single developer machine |
| **Ledger exported** | 2026-07-20 — 119 turns across four sessions |
| **Impact** | A single turn that produced 3,080 tokens of output was billed **$20.6988 (provider-reported list-price equivalent)**, of which $19.50 is the cache-write line for context the session had already paid to cache once. One session's five-day total: **$235.7484**, same basis |
| **Repro** | `python3 repro.py` — offline, no account, ~0.1s |

---

## 1. Symptom

Nothing breaks. That is the entire problem.

The agent works. Turns complete, tools run, answers arrive, tests pass. There is
no error to grep for, no failed turn, no latency cliff, no alert. The only
symptom is a usage figure much larger than the work appears to justify, and by
the time you look at it the sessions that produced it are days old.

When you finally do look, the shape is counterintuitive in a specific way:

- The expensive turns are not the ones where the model thought hardest. In
  session A, the model generated 197,896 tokens across the whole session, and
  the output line is **9.2%** of the session's cost.
- The expensive turns are not the long ones. Session B's most expensive turn
  ran for **131** seconds and produced **3,080** tokens. It was billed
  **$20.6988** — twelve times the session's median turn.
- Two sessions doing completely different work were billed large rewrites
  **81** seconds apart, both after the same long idle period. Neither session's
  activity explains the other's charge.

The mental model that makes this invisible is the natural one: *cost tracks
effort*. Hard question, big bill. In these traces it does not. What the cost
tracks is **how many context tokens moved**, and that quantity is set by the
size of the session's history and the number of model calls in a turn — not by
how much thinking happened inside them.

## 2. Root cause — one architecture, two ways to pay for it

> **`[operator testimony]` — the incident narratives below (what was being
> worked on, what was noticed when, what was concluded) come from the
> operator's diagnostic notes written on 2026-07-09 and 2026-07-15. The
> *numbers* are not testimony: they are read out of the usage ledger, and
> where the notes and the ledger disagree the ledger is used and the
> disagreement is recorded in [§3](#3-evidence).**

Both modes fall out of one property of the architecture. The model is
stateless, and an agentic turn is a loop: the harness sends the whole context,
the model calls a tool, the harness appends the result and sends **the whole
context again**. A turn with ten tool calls is eleven full-context requests.

Prompt caching makes this affordable, not free. A cached prefix bills at 0.1×
the input rate instead of 1×. That is a 90% discount on a quantity that grew by
two orders of magnitude, and the discount is why nobody notices: the *per-token*
price collapsed, so the *total* can climb without ever looking wrong.

### Mode A — the hot re-read (diagnosed 2026-07-09)

`[operator testimony]` A marathon session, run to a near-full context window,
worked for just under four hours across **20** turns with roughly ten tool calls
each.

The ledger's account of it — a decomposition of the provider-reported costs,
every turn of which reconciles (see [§3](#3-evidence)):

| | tokens | list-price equivalent | share |
|---|---:|---:|---:|
| input (uncached) | 38,511 | $0.19 | 0.4% |
| **cache read** | **43,715,956** | **$21.86** | 40.6% |
| **cache write** | **2,685,449** | **$26.85** | 49.9% |
| output (generation) | 197,896 | $4.95 | **9.2%** |
| | | **$53.85** | |

43.7 million cached tokens read, to produce 197,896 tokens of output — a ratio
of **220.9** to 1. The three context lines are **90.8%** of the total; the
output line is 9.2%.

The single worst turn read **11,289,438** cached tokens. The context window is
1,000,000 tokens, and no single request can read more cached tokens than fit in
the window — so that one turn made **at least 11.3** model calls, each
re-sending a substantial context. That is a floor derived from the window, not
a count: the ledger aggregates a whole turn into one row and cannot see
individual calls. It is the strongest form the "~10 tool calls per turn" claim
can honestly take.

**The wrong path taken first.** `[operator testimony]` The initial suspicion was
that the model was thinking too hard — that the fix was a lower effort setting
or a terser prompt. The decomposition redirects that: the output line is 9.2% of
this session's cost, so tuning generation is working on the smallest of the four
lines. (What it does *not* establish is how much a terser configuration would
have saved overall — output feeds back into the context that later turns
re-read, and a different verbosity or effort setting also changes how many tool
calls a turn makes. The 9.2% bounds the **direct** output line, not the
counterfactual.)

### Mode B — the cold rewrite (diagnosed 2026-07-15)

Prompt caches expire. A request that finds its prefix still cached bills the
read rate; one that does not must write the prefix back, and **a cache write
bills at 2× the input rate against a read's 0.1×**. Same tokens, twenty-fold
difference in unit price, decided by whether the cache entry was still there.

That is the mechanism. What the ledger shows is its footprint: turns carrying
enormous `cache_creation` counts, ordinary output, and — usually, but not
always — a long preceding gap. Session B, opened 2026-07-11 and still alive on
2026-07-15, has several across its **66** turns. Its largest:

| | |
|---|---|
| turn | 2026-07-15 06:06:34 |
| idle since the previous billed turn | **4.1** hours |
| cache **write** | **974,755** tokens — against a 1,000,000-token window |
| cache read | 1,049,591 tokens |
| output | **3,080** tokens |
| duration | **131** seconds |
| **provider-reported cost** | **$20.6988** |
| of which the cache-write line | **$19.50** — **94.2%** |
| the output line | $0.15 |

$20.70 to produce 3,080 tokens. The session had built that context before; this
turn is billed for putting it back.

The rewrite is charged **per session**, which is the part that scales badly.
**81** seconds after session B's turn, **session C** — a different session,
different task, idle across the same period — was billed **$3.8608** for
rewriting its own **175,728** tokens. One walk away from the keyboard, two
rewrites. Neither session's own activity explains the other's charge; what they
share is the idle period. Whether the elapsed time *caused* either rewrite is a
question the traces do not settle — see [§3](#3-evidence), which is where this
entry stops.

Across 2026-07-15, session B's cache-write lines on the **five** turns rewriting
≥400k tokens total **$56.80**.

### Why the two modes are the same bug

Mode A is the cost of *using* a large context. Mode B is the cost of
*re-warming* one. Both scale with context size rather than with work done, and
both are invisible in any metric an agent platform normally shows you — turns,
latency, errors, tokens generated. The quantity that tracks the bill is the one
nobody displays: **how much context this session is carrying.**

## 3. Evidence

Three evidentiary bases, deliberately not mixed.

### The billing reconstruction — `trace replay`

119 per-turn usage records from four real sessions, redacted and committed to
[`evidence/`](evidence/) with a published transform and a `--check` mode. Full
provenance and redaction log: [`evidence/README.md`](evidence/README.md).

The load-bearing move is that **the pricing model is validated, not assumed.**
`repro.py` oracle 1 reconstructs each turn's cost from published list prices and
the token counters, and compares against the cost the CLI itself reported:

| cache-write rate assumed | turns reconciling to within 0.1% |
|---|---:|
| 2.00× input (the **1-hour** TTL rate) | **95** of **99** priced turns |
| 1.25× input (the 5-minute TTL rate) | **0** of 99 |

Two things follow, and a third does not.

**It does follow** that the per-line decomposition is sound for the 95 turns
that reconcile: for those, the formula reproduces the provider's own arithmetic,
so splitting a turn into read / write / output lines is reading the provider's
own model back out, not estimating.

**It also follows** that the CLI's cost calculator is applying the **1-hour**
cache SKU, since the two TTLs have different published write multipliers and
only one of them fits.

**It does not follow** that any particular cache entry survived for an hour and
then expired. The reconciliation identifies the *rate being billed*, which is a
fact about the price list, not about the lifetime of a specific cache entry.
Mode B's mechanism is stated on that basis and no stronger.

**Two figures in this entry are provider-reported totals rather than
decompositions**, and the difference matters because **four turns do not
reconcile**. Session B's **$235.7484** lifetime and session A's **$53.85** are
sums of the `cost` field as reported, unreconciled turns included. The per-line
tables are decompositions and cover only reconciling turns. The strangest
non-reconciling row is an errored turn billed **$9.8231** with every token
counter at zero — a charge with no recorded consumption. I do not know what it
is. `claims.py` pins the count at **four** so this paragraph cannot quietly
become "everything reconciles".

### What the ledger says that the notes got wrong

The notes were written during the incidents; the ledger was read five days
later. Four corrections, recorded rather than silently fixed:

| the notes said | the ledger says |
|---|---|
| "43.7M cache-read tokens ≈ $54" | 43,715,956 cache-read tokens is right, but **$53.85 is the whole session**. The cache-read line alone is **$21.86**. The notes conflated a line item with a total |
| session B's lifetime cost was **$212** | **$235.7484** |
| "four wakeup taxes that day, about $50" | on 2026-07-15 there were **five** turns rewriting ≥400k tokens, whose cache-write lines total **$56.80** |
| the session held **1,042 messages** | **unverifiable.** Session B's row in the `sessions` table and all of its messages have since been deleted; only the usage ledger survives. The "near-1M context" claim is corroborated differently — by the 974,755-token rewrite against a 1,000,000-token window |

And one correction to the entry's own framing. The 07-09 note says the money
went to *re-reading*. Directionally that is right — the context lines are 90.8%
of the bill against 9.2% for output — but within session A the **write** line
($26.85) is actually larger than the **read** line ($21.86). "It's the re-reads"
is the memorable version; "it's the context, in both directions" is the true one.

### Where the causal claim stops — and this is the important part

The natural sentence to write is *"the session sat idle past the TTL, so the
cache expired, so the next turn rewrote it."* **This entry does not claim that**,
for any turn, including the $20.6988 one. What it claims is an association
within four traces, a documented pricing mechanism that would explain it, and
nothing joining the two.

Pooled across the three claude-code traces:

| | turns | median share of context rewritten |
|---|---:|---:|
| after a >1h gap | **20** | **28.1%** |
| after a ≤1h gap | **75** | **1.2%** |

That pooled contrast is **descriptive, not inferential**, and three things
undercut reading it as a predictive result:

- **The turns are nested in three sessions, not 95 independent samples.** Split
  by session, the effect is not consistent — **it reverses in session A**:

  | trace | median after >1h | median after ≤1h |
  |---|---:|---:|
  | session A | **0.2%** | **5.8%** |
  | session B | **47.4%** | **0.8%** |
  | session C | **20.6%** | **3.1%** |

- **Not sufficient.** Some turns following gaps of over an hour rewrote as
  little as 0.2% of their context.
- **Not necessary.** Some turns following gaps of *minutes* rewrote 93% of
  their context. In session B, three consecutive turns within seven minutes each
  rewrote ~370,000 tokens — elapsed time cannot explain that, and I do not know
  what does. Cache-prefix invalidation is the obvious candidate and I have no
  evidence for it.

So: **exceeding the cache TTL is neither necessary nor sufficient for a rewrite
spike in these traces.** `repro.py` oracle 4 asserts the pooled contrast *and*
both counterexamples, so the prose cannot drift into a causal claim later.

**The instrumentation confound, which nearly manufactured a result.** The
**codex** backend reports `cache_creation_tokens` as 0 on *every* turn — not
because nothing is written, but because it does not report the field.
[`evidence/codex_no_cache_field_D.jsonl`](evidence/codex_no_cache_field_D.jsonl)
is a real **6**-turn codex session included for exactly this purpose: it
contains a **132**-minute gap followed by a turn reporting a 0-token rewrite.
Pooled in naively, turns like it form a tidy population of "long-gap turns that
rewrote nothing" which is pure instrumentation artifact. Oracle 4 excludes the
codex backend, and `test_trace_the_codex_exclusion_is_not_vacuous` asserts the
trace is non-empty *before* asserting the zeros — an earlier revision made this
claim with no codex data committed at all, so the test passed on an empty list
and proved nothing. Review caught it.

### The counterfactual — `mechanism simulation`, and nothing more

The ledger records what the marathon sessions were billed. It cannot record what
the same work would have cost under different session hygiene, because that run
never happened. [`simulate.py`](simulate.py) runs both arms against a fake clock
and a fake TTL cache: 16 synthetic tasks, an identical output-token budget in
both arms, one arm in a single growing session and one retiring the session at a
context watermark.

It reports **1.84×**. That number is a property of parameters I chose — task
count, context growth, idle length — and would change if I chose differently. It
is here to show the mechanism has the claimed *shape*, and it is quarantined in
its own file, behind its own banner, with its own evidence grade. **No table in
this entry mixes a simulated number with a measured one.**

### Known limits

One machine, one operator, one plan tier, four sessions, twelve days. The
sessions cannot be re-run — session B no longer exists. The ledger has no
per-call resolution, so the step-count claim is a floor rather than a count.

### On the money

Every figure here is a **provider-reported list-price equivalent**. The
reconciliation above is what proves the distinction is real rather than
pedantic: a `cost` field that reproduces published list prices to the cent is a
*computation*, not a settlement. These sessions ran on a subscription plan. No
billing statement was consulted, no charge record exists in hand, and this entry
does not claim one. "This turn was billed $20.6988" means "this turn consumed
tokens worth $20.6988 at list price".

## 4. Minimal case

```bash
cd entries/context-burn-two-modes
python3 repro.py        # exit 0 = every oracle held   (trace replay)
python3 simulate.py     # exit 0 = every oracle held   (simulation)
```

Offline, no account, no network, no API spend, ~0.1s each. Standard library
only; imports nothing from Owlery. `repro.py` reads committed traces off disk
and never opens a database.

**`repro.py` — four machine-checked oracles over the real traces:**

1. **The pricing model reproduces the provider's own cost**, ≥90% of priced
   turns to within 0.1% — and the 5-minute-TTL rate reproduces **none** of them.
   The second half is the load-bearing one: a model that fitted both would
   identify no cache SKU at all.
2. **Mode A**: the context lines are ≥90% of session cost; ≥200 cached tokens
   read per token produced; and at least one turn provably re-read the context
   >10× over, derived from the window size.
3. **Mode B**: the largest rewrite follows a >1h gap, rewrites ≥900k tokens,
   was billed >$20 with >90% of it in the cache-write line, and ran >10× the
   session median — and a second session was billed its own rewrite within 2
   minutes.
4. **The limits of the causal claim**: asserts the pooled association *and* both
   counterexamples — that long gaps sometimes rewrite nothing and short gaps
   sometimes rewrite everything. An oracle that pins what the entry **cannot**
   conclude, so §3 cannot quietly grow a stronger claim later.

**`simulate.py` — the counterfactual**, prominently labelled, five oracles
including that both arms spend an identical output-token budget (so the defense
cannot look good by simply doing less).

**Regression tests and the claims checker:**

```bash
python3 test_defense.py            # or, if pytest is installed:
python3 -m pytest test_defense.py -q
python3 claims.py --check          # every quoted number, vs traces AND prose
```

`test_defense.py` pins the proposed defense and the structural properties of the
traces. `claims.py` is the answer to a question an earlier revision got wrong:
it holds **every figure this entry quotes**, recomputes each from the committed
traces, *and* asserts the literal string still appears in this README. Both
halves are needed — the earlier revision claimed its tests "pin the prose", but
no test read this file, so README-only drift was permanently green, and a trace
row that no test happened to name could be edited with everything still passing.
A reviewer demonstrated both holes. `claims.py --check` closes them, and
carries a SHA-256 of every trace file besides, so that an edit to a row no
claim happens to name is caught too — the reviewer demonstrated that gap as
well, by adjusting an unnamed row consistently enough to keep the pricing
model happy.

One test exists because of a bug in this entry's own simulation.
`test_defense_is_inert_when_the_watermark_is_never_reached` pins a case that
shipped broken in the first draft: the invented workload topped out at 430k
tokens against a 500k watermark, so the "defended" arm never fired and was
byte-identical to the marathon. The comparison was vacuous and an oracle caught
it — which is the argument for oracles that assert a *difference* rather than
printing two numbers for a human to eyeball. It is the same class of bug as the
empty codex assertion in §3, found the same way.

## 5. Discrimination and defense

### Diagnosing it, in the order that costs least

1. **Decompose the bill before theorising about it.** Split cost into
   input / cache-read / cache-write / output. Every useful conclusion in this
   entry came from that one table. If the output line is a single-digit
   percentage, generation tuning is working on the smallest line — though note
   the caveat in §2: that bounds the direct line, not the total saving.
2. **Sort turns by cost and look at what each one rewrote.** Rewrite-heavy turns
   have a signature: enormous `cache_creation`, ordinary `output`. They are
   unmissable once you look and invisible until you do.
3. **Count sessions, not turns.** The rewrite is charged per session. Two idle
   marathon sessions are billed two rewrites, for zero extra work — as sessions
   B and C were, 81 seconds apart.
4. **Only then look at the model's behaviour.** "It's thinking too much" is the
   hypothesis of last resort. It was the hypothesis of first resort.

### The defense

Two operational rules, and one thing to build.

**One task, one session.** A session's context is a monotonically growing
liability, and it does not stop being one when you stop typing.

**Prefer a fresh session over a large idle one for unrelated work.** In these
traces the rewrite line was charged on the first turn back regardless of how
small that turn's own output was — $19.50 of cache-write against $0.15 of
output, in the case above.

**And the thing to build — a context watermark:**

```python
def should_retire_session(context_tokens, window, threshold=0.50):
    """Retire the session rather than hand it the next task."""
    return context_tokens >= window * threshold
```

That is the whole defense, and it needs no knowledge of what the session is
doing. **It does, however, need instrumentation that does not exist yet** — see
below. `test_defense.py` pins that it fires exactly at its threshold, that it
does not fire below, that it reduces spend on identical synthetic work, and that
it does not achieve that by spending a smaller output budget.

### What Owlery actually does — reported as found

**Owlery has no context-level visibility, its design encourages exactly the
sessions that make that expensive, and — the part I got wrong first — it does
not currently record the number the defense needs.** All three are still true as
of 2026-07-20.

The platform pushes work *into* long-lived sessions by design:

- a messaging-bridge chat binds to an agent with a **sticky session** — every
  message for that chat lands in the same session, indefinitely;
- **delegation replies** are injected as follow-up turns into the *calling*
  session;
- **background-task results** are injected the same way, into the session that
  started the task.

Every one of those is a good feature. Together they mean the default lifecycle
of a useful Owlery session is *forever*, and nothing anywhere tells the operator
what that is costing.

**What is missing is the numerator, not the denominator.** An earlier revision of
this section claimed the fix needed no new instrumentation, because the CLI
already reports `contextWindow` on every turn and Owlery stores it. A reviewer
pointed out that this is the *window capacity* — the denominator. The defense
needs the session's **current context size**, and that is not in the ledger:
`_record_turn_usage` writes one row per turn from the CLI's final result event,
so `cache_read_tokens` is a whole-turn aggregate across every model call in that
turn and can exceed the window many times over — 11.3× in session A's worst
turn. It is not a context level and must not be used as one.

So the honest statement is: the watermark needs a new field — the per-request
context size, captured at the harness boundary and stored per turn — plus a
place to surface it. What exists today is a usage API that aggregates
**backwards** over a time window: how much was spent, after it was spent. There
is no forward-looking signal, and nothing in the UI distinguishes a fresh
session from one carrying 900k tokens of history.

The proposal — record the level, surface it, warn at a threshold — has been
written down and **not scheduled**. It is reported here as a known defect with a
known fix, not as work completed.

> **The lesson generalizes past agents.** When a discount is large enough, it
> stops being a saving and becomes a *blind spot*. Prompt caching cut the price
> of a re-sent context by 90%, which is exactly why a 220-to-1 read amplification
> could run for four hours without anything looking wrong. The per-unit number
> got better; the total got worse; and only the per-unit number was on screen.
> **Instrument the quantity that scales, not the one that got cheaper.**

**Detection in production**: one gauge and one counter. Gauge — context tokens
as a fraction of the window, per live session (needs the field above). Counter —
turns whose `cache_creation` exceeds some fraction of their total context, which
*is* computable from what is already stored. The first tells you which sessions
are expensive to keep warm; the second tells you when you were billed to
re-warm one. Neither needs to know what the agent is doing.

---

## 中文摘要

> 全文金额一律为 **provider-reported list-price equivalent(供应商报告的列表价等值)**,**不是扣款**;从未查阅任何账单。原因见下方证据段。

**现象**:什么都没坏——这正是问题所在。agent 一切正常:turn 正常完成、工具正常调用、答案正常返回、测试正常通过。没有可以 grep 的错误、没有失败的 turn、没有延迟异常、没有任何告警。唯一的症状是一个与工作量明显不相称的用量数字,而等你去看的时候,产生它的会话已经是几天前的事了。真正去看时,形状是反直觉的:**最贵的 turn 不是模型思考最多的那些**——A 会话整场生成了 197,896 个输出 token,输出这一行只占该会话成本的 **9.2%**;**最贵的 turn 也不是最长的那些**——B 会话最贵的一轮只跑了 **131** 秒、只产出 **3,080** 个 token,却被计费 **$20.6988**,是该会话中位 turn 的 12 倍;而且两个做着完全不同工作的会话,在同一段闲置之后相隔 **81** 秒各自被计入一笔巨额重写,**任一会话自身的活动都无法解释另一笔账**。让这一切隐形的,是那个最自然的心智模型:*成本随付出的努力走*。在这些 trace 里它不随。成本跟着的是**移动了多少上下文 token**,而这个量由会话历史的大小与一轮中的模型调用次数决定,**而不是由其中发生了多少思考决定**。

**根因(两种模式,同一个架构)**:模型是无状态的,而一个 agentic turn 是一个循环——harness 发送全部上下文,模型调用一次工具,harness 追加结果后**再次发送全部上下文**。一个包含十次工具调用的 turn,就是十一次全量上下文请求。缓存让这件事变得可负担,但不是免费:命中的前缀按输入价的 0.1× 计费。这个九折优惠正是没人察觉的原因——**单价**塌缩了两个数量级,于是**总额**可以一路攀升而始终看起来不像出了问题。
- **模式 A「热重读」(07-09 诊断)**:一个近满仓的马拉松会话,**20** 个 turn、每轮约十次工具调用,跑了近四小时。账本的分解(这些轮次全部对账通过):cache-read **43,715,956** token($21.86,40.6%)、cache-write 2,685,449 token($26.85,49.9%)、输出 197,896 token($4.95,**9.2%**)、未缓存输入 $0.19,合计 **$53.85**。读了 4370 万缓存 token,只为产出 19.7 万输出 token,比值 **220.9 : 1**;三条上下文行合计占账单 **90.8%**,输出行占 9.2%。**先走的弯路**:最初怀疑是模型想得太多、该调低 effort 或压缩提示词;分解把注意力引开了——输出行只占 9.2%,调生成是在四条线里最小的那条上用力。**但必须说清楚**:9.2% 约束的是**直接的输出行**,**不是**「让模型少说话能省多少」这个反事实——输出会进入后续轮次重读的上下文,而改变 verbosity 或 effort 同样会改变一轮里的工具调用次数。单轮最差的一次读了 **11,289,438** 个缓存 token,而窗口是 1,000,000 token,单次请求读不到超过窗口容量的缓存,所以那一个 turn **至少发生了 11.3** 次模型调用——这是由窗口推出的**下界**,不是计数。
- **模式 B「冷重写」(07-15 诊断)**:缓存会过期。请求若发现前缀仍在缓存中,按读取价计费;若不在,就必须把前缀写回去,而**写入按输入价的 2× 计费,读取按 0.1×**。同样的 token,单价相差二十倍,取决于那条缓存条目还在不在。**这是机制**;账本显示的是它的足迹:一些 turn 带着巨大的 `cache_creation`、平常的输出,并且——**通常但并非总是**——前面有一段长间隔。B 会话最大的一次:07-15 06:06:34,距上一个计费轮闲置 **4.1** 小时,cache-**write 974,755** token(窗口 1,000,000)、cache-read 1,049,591、输出仅 **3,080**、耗时 **131** 秒、**供应商报告成本 $20.6988**,其中 cache-write 行占 **$19.50(94.2%)**,输出行 $0.15。重写按**会话**计费,这是扩散得最糟的一点:B 会话那笔之后 **81** 秒,**C 会话**(不同会话、不同任务、只是一起闲置)因重写自己的 **175,728** token 被计费 **$3.8608**。**任一会话自身的活动都不能解释另一笔账;它们共有的是那段闲置。至于流逝的时间是否「导致」了任何一笔重写,trace 并不能定论**——见下方证据段,本条目在那里止步。07-15 当天,B 会话在**五**个重写 ≥400k token 的轮次上,cache-write 行合计 **$56.80**。
- **两种模式其实是同一个 bug**:A 是**使用**大上下文的代价,B 是**重新捂热**大上下文的代价。两者都随上下文大小而非实际工作量增长,且都不出现在 agent 平台通常展示的任何指标里(轮次、延迟、错误、生成 token 数)。真正跟着账单走的那个量,恰恰是没人显示的那个:**这个会话正背着多少上下文。**

**证据等级(两级,严格不混用)**:计费重建是 `trace replay`——`repro.py` 回放四个真实会话共 119 条脱敏逐轮用量记录。**关键一步是计价模型经过验证而非假定**:用公开 list price 重算每轮成本,与 CLI 自报的成本比对,在 **2.00×**(**1 小时** TTL 写入价)下 **99 个计费轮中 95 轮**误差 <0.1%,而按 5 分钟 TTL 的 1.25× **一轮都对不上**。由此**能**推出两件事:(a) 对那 95 轮,逐行分解是把供应商自己的算术读回来,不是估算;(b) CLI 的成本计算器用的是 **1 小时**缓存 SKU。但**不能**推出的是:**任何一条具体缓存条目「存活了一小时后过期」**——这个对账识别的是**被计费的费率**(价目表的事实),不是某条缓存条目的生存期;模式 B 的机制只按这个强度陈述,不再更强。**有两个数字是供应商报告的总额而非分解**,这个区别之所以重要,是因为**有四轮无法对账**:B 会话一生 **$235.7484** 与 A 会话 **$53.85** 都是 `cost` 字段的原样求和(含未对账轮次),而逐行表是分解、只覆盖可对账轮次。最离奇的一条是**所有 token 计数为零却被计费 $9.8231** 的错误轮次,我不知道那是什么。反事实部分是 `mechanism simulation`:账本记录了马拉松会话被计费多少,却无法记录**同样的工作在不同会话卫生下会花多少**,因为那次运行从未发生;`simulate.py` 用假时钟与假 TTL 缓存跑两条臂(16 个合成任务、两臂**输出 token 预算相同**),报出 **1.84×**——**这个数字是我所选参数的性质**,换参数就变,它只用来展示机制的**形状**,并被隔离在独立文件、独立横幅、独立评级之下;**本条目没有任何一张表把模拟数字与实测数字混在一起。**

**账本纠正了当时笔记的四处错误(如实记录而非悄悄改掉)**:(a) 「4370 万 cache-read ≈ $54」——token 数没错,但 **$53.85 是整场总额**,cache-read 行本身只有 **$21.86**;(b) B 会话一生笔记记为 **$212**,账本是 **$235.7484**;(c) 「当天四次唤醒税约 $50」实为**五**个轮次、cache-write 行共 **$56.80**;(d) 「1,042 条消息」**无法核实**——session 行与全部消息此后已被删除,只剩用量账本;「近 1M 上下文」改由那次针对 1,000,000 token 窗口的 974,755 token 重写佐证。此外还纠正了本条目自身的措辞:笔记说钱花在**重读**上,方向是对的(上下文行 90.8% vs 输出 9.2%),但 A 会话内部**写入行 $26.85 其实大于读取行 $21.86**。

**因果claim到哪里为止(最重要的一节)**:最顺手的句子是「会话闲置超过 TTL,所以缓存失效,所以下一轮重写了它」。**本条目不对任何一轮做此主张,包括那笔 $20.6988。** 它主张的是:四条 trace 内的一个关联、一个有据可查的计价机制、以及**两者之间没有连线**。三条 claude-code trace 汇总:>1h 间隔 **20** 轮、重写占比中位数 **28.1%**;≤1h 间隔 **75** 轮、中位数 **1.2%**。但这个汇总对比是**描述性的,不是推断性的**,有三点削弱把它读成预测结论:**其一,这些轮次嵌套在三个会话里,不是 95 个独立样本**——按会话拆开效果并不一致,**在 A 会话甚至反向**(A:长间隔 **0.2%** vs 短间隔 **5.8%**;B:**47.4%** vs **0.8%**;C:**20.6%** vs **3.1%**);**其二,不充分**——有 >1h 间隔只重写 0.2% 的;**其三,不必要**——有 ≤1h 间隔重写 93% 的,B 会话里甚至有连续三轮在七分钟内各自重写约 37 万 token,流逝时间解释不了,而我**不知道**什么能解释(缓存前缀失效是显而易见的候选,但我没有证据)。所以:**在这些 trace 中,超过缓存 TTL 对重写尖峰既不充分也不必要。** 另有一个**差点炮制出假结果的仪表混淆项**:**codex** 后端在**每一轮**都把 `cache_creation_tokens` 报成 0——不是因为没有写入,而是因为它不报告该字段;`evidence/codex_no_cache_field_D.jsonl` 是一个真实的 **6** 轮 codex 会话,专为此收录,其中就有一段 **132** 分钟间隔后「重写 0 token」的轮次。天真地汇总进去,这类轮次会形成一批「长间隔却毫无重写」的漂亮样本,而那纯属仪表假象。oracle 4 排除了 codex 后端,并且 `test_trace_the_codex_exclusion_is_not_vacuous` 会**先断言该 trace 非空**再断言那些零值——早先的版本在**根本没有提交任何 codex 数据**的情况下就写下了这个说法,于是测试对着空列表通过、什么也没证明,是复核抓出来的。

**防御**:两条操作纪律加一件该建的东西。**一任务一会话**——会话的上下文是单调增长的负债,不会因为你停止打字就不再是负债。**无关的新工作宁可开新会话,也不要丢给一个大的闲置会话**——在这些 trace 里,回来的第一轮无论自身产出多小都被计入了重写行(上例是 $19.50 的写入对 $0.15 的输出)。**该建的东西是上下文水位线**:`context_tokens >= window * threshold` 就退休该会话。整个防御就这一行,不需要知道会话在做什么,**但它需要一项目前并不存在的埋点**(见下)。

**Owlery 的真实情况——如实报告,三点截至 2026-07-20 均成立**:**Owlery 没有上下文水位可见性;它的设计恰好鼓励那些让这件事变贵的会话;而且——这一点是我最初写错、被复核指出的——它目前并不记录防御所需的那个数。** 平台按设计把工作**推入**长驻会话:消息桥的一个聊天以**粘性会话**绑定到 agent;**委派回复**注入**调用方**会话;**后台任务结果**注入发起它的会话。这些**每一个都是好功能**,合起来却让一个有用的 Owlery 会话默认活到**永远**,而没有任何地方告诉操作者这正在花多少钱。**缺的是分子,不是分母**:早先版本声称「无需新埋点,因为 CLI 每轮都报 `contextWindow` 且 Owlery 存了下来」——复核指出那是**窗口容量,即分母**;防御需要的是会话**当前的上下文大小**,而它不在账本里:`_record_turn_usage` 依据 CLI 的最终结果事件每轮写一行,因此 `cache_read_tokens` 是**整轮跨所有模型调用的聚合**,可以数倍于窗口(A 会话最差轮是 **11.3** 倍),**它不是上下文水位,也不能当作水位使用**。所以诚实的说法是:水位线需要**新增一个字段**(在 harness 边界捕获每次请求的上下文大小并按轮存储),外加一个展示它的地方。今天存在的,是一个**向后**按时间窗聚合的用量 API:花了多少,在花完之后;**没有任何前瞻信号**,UI 里也没有任何东西能区分一个全新会话和一个背着 90 万 token 历史的会话。该提案(记录水位、显示、到阈值提醒)**已写下但未立项**,在此作为「已知缺陷 + 已知修法」报告,而**不是**已完成的工作。

**通用教训**:**当一个折扣大到一定程度,它就不再是节省,而变成盲区。** 缓存把重发上下文的价格砍掉 90%,而这恰恰是「220 : 1 的读放大」能连跑四小时而毫无异样的原因——**单位数字变好了,总额变坏了,而屏幕上只有单位数字。要给会规模化的那个量装仪表,而不是给变便宜的那个量装。** 生产检测:一个 gauge(每个活跃会话的上下文 token 占窗口比例,需要上面那个新字段)加一个 counter(`cache_creation` 超过自身总上下文某一比例的轮次数,这个**用现有存储就能算**);前者告诉你哪些会话「捂热很贵」,后者告诉你何时被计费去重新捂热一个会话。
