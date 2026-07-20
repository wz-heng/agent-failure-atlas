# Two ways a long-context agent session burns money, neither of them thinking

> **Evidence level: `trace replay`** — for the billing reconstruction.
> [`repro.py`](repro.py) replays a real per-turn usage ledger
> ([`evidence/`](evidence/)) and rebuilds both burn modes from it, including
> the decomposition that shows 91% of one session's cost was moving context
> around rather than generating anything.
>
> **Evidence level: `mechanism simulation`** — for the counterfactual.
> [`simulate.py`](simulate.py) uses a fake clock and a fake TTL cache to run
> the arm the ledger cannot contain: *the same work under different session
> hygiene*. Its numbers are invented and are never mixed with the measured
> ones. The two live in separate files for that reason.
>
> The 2026-07-09 and 2026-07-15 diagnoses themselves are **operator
> testimony**. Where the ledger disagrees with the notes, the ledger wins and
> [§3](#3-evidence) says so — it disagreed four times.

| | |
|---|---|
| **Hit in** | Owlery — a personal agent platform driving the Claude Code and Codex CLIs (private repository) |
| **Systems** | Claude Code CLI against `Claude-opus-4-8` and `claude-fable-5`, 1M-token context window, 1-hour prompt cache |
| **Platform** | macOS 26.3 (Darwin 25.3.0), Python 3.12 |
| **Incidents** | 2026-07-09 and 2026-07-15, single developer machine |
| **Ledger exported** | 2026-07-20 — 113 turns across three sessions |
| **Impact** | A single turn that produced 3,080 tokens of output cost **$20.70**, of which $19.50 was rewriting context the session had already paid for once. One session's five-day list-price total: **$235.75** |
| **Repro** | `python3 repro.py` — offline, no account, ~0.1s |

---

## 1. Symptom

Nothing breaks. That is the entire problem.

The agent works. Turns complete, tools run, answers arrive, tests pass. There is
no error to grep for, no failed turn, no latency cliff, no alert. The only
symptom is a number on a billing page that is much larger than the work
appears to justify, and by the time you look at it the sessions that produced
it are days old.

When you finally do look, the shape is counterintuitive in a specific way:

- The expensive turns are not the ones where the model thought hardest. In
  session A, the model generated 197,896 tokens across the whole session and
  that generation accounts for **9.2%** of the cost.
- The expensive turns are not the long ones. Session B's most expensive turn
  ran for **131 seconds** and produced **3,080 tokens**. It cost **$20.70** —
  twelve times the session's median turn.
- Two sessions doing completely different work can spike at the same instant,
  81 seconds apart, for the same reason — and that reason is *neither
  session's* fault.

The mental model that makes this invisible is the natural one: *cost tracks
effort*. Hard question, big bill. It does not. **Cost tracks context length,
almost independently of how much thinking happens inside it.** A session with a
near-full context window is expensive to talk to *at all*, and a session with a
near-full context window that you have not spoken to in an hour is expensive
before it has done anything.

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
worked for just under four hours across 20 turns with roughly ten tool calls
each.

The ledger's account of it:

| | tokens | list-price cost | share |
|---|---:|---:|---:|
| input (uncached) | 38,511 | $0.19 | 0.4% |
| **cache read** | **43,715,956** | **$21.86** | **40.6%** |
| **cache write** | **2,685,449** | **$26.85** | **49.9%** |
| output (generation) | 197,896 | $4.95 | 9.2% |
| | | **$53.85** | |

43.7 million cached tokens read, to produce 197,896 tokens of output — a ratio
of **220.9 to 1**. Context handling is 90.8% of the bill; everything the model
actually said is 9.2%.

The single worst turn read **11,289,438 cached tokens**. The context window is
1,000,000 tokens, and no single request can read more cached tokens than fit in
the window — so that one turn made **at least 11.3 model calls**, each
re-sending a substantial context. That is a floor derived from the window, not
a count: the ledger aggregates a whole turn into one row and cannot see
individual calls. It is the strongest form the "~10 tool calls per turn" claim
can honestly take.

**The wrong path taken first.** `[operator testimony]` The initial suspicion
was that the model was thinking too hard — that the fix was a lower effort
setting or a terser prompt. The output column refutes it: at 9.2% of cost, you
could have made the model *silent* and saved less than a tenth of the bill.

### Mode B — the cold rewrite (diagnosed 2026-07-15)

The cache has a time-to-live. When it lapses, the next turn does not re-read the
context — it **rewrites** it. And a cache write does not cost 0.1× the input
rate; it costs **2×**.

That is a 20-fold swing in the price of the same tokens, triggered by nothing
but elapsed time.

Session B, opened 2026-07-11 and still alive on 2026-07-15, hit this repeatedly.
Its worst instance:

| | |
|---|---|
| turn | 2026-07-15 06:06:34 |
| idle before it | **4.1 hours** |
| cache **write** | **974,755 tokens** — against a 1,000,000-token window |
| cache read | 1,049,591 tokens |
| output | 3,080 tokens |
| duration | 131 seconds |
| **provider-reported cost** | **$20.6988** |
| of which the rewrite | **$19.50 — 94.2%** |
| the model's actual output | $0.15 |

$20.70 to say 3,080 tokens' worth of anything. The session had done this work
before; it was paying to put it back.

The rewrite is **per session**, which is the part that scales badly. 81 seconds
after session B's $20.70 turn, **session C** — a different session, different
task, idle across the same period — paid **$3.8608** to rewrite its own 175,728
tokens. One walk away from the keyboard, two taxes. Nothing either session did
caused the other's charge; the common cause was the clock.

Across 2026-07-15 alone, session B paid **$56.80** in cache-write charges across
five turns.

### Why the two modes are the same bug

Mode A is the cost of *using* a large context. Mode B is the cost of *keeping*
one. Both follow from context length rather than from work done, and both are
invisible in any metric an agent platform normally shows you — turns, latency,
errors, tokens generated. The quantity that predicts the bill is the one nobody
displays: **how much context this session is carrying.**

## 3. Evidence

Three evidentiary bases, deliberately not mixed.

### The billing reconstruction — `trace replay`

113 per-turn usage records from three real sessions, redacted and committed to
[`evidence/`](evidence/) with a published transform and a `--check` mode. Full
provenance and redaction log: [`evidence/README.md`](evidence/README.md).

The load-bearing move is that **the pricing model is validated, not assumed.**
`repro.py` oracle 1 reconstructs each turn's cost from published list prices and
the token counters, and compares against the cost the CLI itself reported:

| cache-write rate assumed | turns reconciling to within 0.1% |
|---|---:|
| 2.00× input (the **1-hour** TTL rate) | **95 of 99** |
| 1.25× input (the 5-minute TTL rate) | **0 of 99** |

Two things follow. First, every dollar figure in this entry is a *decomposition
of a provider-reported number*, not an estimate — the formula demonstrably
reproduces the provider's own arithmetic. Second, the cache in play is the
**1-hour** TTL, established from billing rather than from anyone's memory. That
matters because mode B's mechanism turns on that TTL.

**Four turns do not reconcile, and they stay in the trace.** The strangest is an
errored turn billed **$9.8231 with every token counter at zero** — a charge with
no recorded consumption. I do not know what it is.
`test_trace_unreconciled_turn_count_is_what_the_entry_says` pins the count at
four, so this paragraph cannot quietly become "everything reconciles".

### What the ledger says that the notes got wrong

The notes were written during the incidents; the ledger was read five days
later. Four corrections, recorded rather than silently fixed:

| the notes said | the ledger says |
|---|---|
| "43.7M cache-read tokens ≈ $54" | 43,715,956 cache-read tokens is right, but **$53.85 is the whole session**. The cache-read line alone is **$21.86**. The notes conflated a line item with a total |
| session B's lifetime cost was **$212** | **$235.7484** |
| "four wakeup taxes that day, about $50" | on 2026-07-15 there were **five** turns rewriting ≥400k tokens, totalling **$56.80** in cache-write charges |
| the session held **1,042 messages** | **unverifiable.** Session B's row in the `sessions` table and all of its messages have since been deleted; only the usage ledger survives. The "near-1M context" claim is corroborated differently — by the 974,755-token rewrite against a 1,000,000-token window |

And one correction to the entry's own framing. The 07-09 note says the money
went to *re-reading*. Directionally that is right — context handling is 90.8%
of the bill against 9.2% for generation — but within session A the **write**
line ($26.85) is actually larger than the **read** line ($21.86). "It's the
re-reads" is the memorable version; "it's the context, in both directions" is
the true one.

### Where the causal claim stops — and this is the important part

The natural sentence to write is *"the session sat idle past the TTL, so the
cache expired, so the next turn rewrote it."* The traces support that **in
aggregate** and refute it **per turn**, and `repro.py` oracle 4 asserts both
halves so the prose cannot drift.

Within the committed traces, counting only claude-code turns:

| | turns | median share of context rewritten |
|---|---:|---:|
| after a >1h gap | 20 | **28.1%** |
| after a ≤1h gap | 75 | **1.2%** |

A 24-fold shift in the median. Real, and large. But:

- **Not sufficient.** Some turns following gaps of over an hour rewrote as
  little as **0.2%** of their context. Exceeding the TTL did not guarantee a
  rewrite.
- **Not necessary.** Some turns following gaps of *minutes* rewrote **93%** of
  their context. In session B, three consecutive turns within seven minutes each
  rewrote ~370,000 tokens — the TTL cannot explain that, and I do not know what
  does. Cache-prefix invalidation is the obvious candidate and I have no
  evidence for it.

So: **idle-past-TTL is neither necessary nor sufficient for a rewrite spike.**
The entry claims the association, the mechanism, and the arithmetic. It does not
claim that any individual spike was caused by the clock — including, strictly,
the $20.70 turn. What is established about that turn is that it followed a
4.1-hour gap, that it rewrote 974,755 tokens, and that the rewrite cost $19.50.
The causal link between the first fact and the second is inference, and it is
labelled as inference here.

One confound worth naming because it nearly manufactured a result: the **codex**
backend reports `cache_creation_tokens` as 0 on every turn — not because nothing
is written, but because it does not report the field. Counting those turns
produced a tidy population of "long-gap turns that rewrote nothing" which was
pure instrumentation artifact. Oracle 4 excludes them and
`test_trace_codex_turns_never_report_cache_creation` pins the exclusion.

### The counterfactual — `mechanism simulation`, and nothing more

The ledger records what the marathon sessions cost. It cannot record what the
same work would have cost under better session hygiene, because that run never
happened. [`simulate.py`](simulate.py) runs both arms against a fake clock and a
fake TTL cache: 16 tasks, identical output either way, one arm in a single
growing session and one retiring the session at a context watermark.

It reports **1.84×**. That number is a property of parameters I chose — task
count, context growth, idle length — and would change if I chose differently.
It is in this entry to show the mechanism has the claimed *shape*, and it is
quarantined in its own file, behind its own banner, with its own evidence grade.
**No table in this entry mixes a simulated number with a measured one.**

### Known limits

One machine, one operator, one plan tier, three sessions, twelve days. The
sessions cannot be re-run — session B no longer exists. Costs are list-price
equivalents and no charge record was consulted (see below). The ledger has no
per-call resolution, so the step-count claim is a floor rather than a count.

### On the money

Every figure here is a **list-price equivalent**, and the reconciliation in this
section is what proves it: a `cost` field that reproduces published list prices
to the cent is a *computation*, not a settlement. These sessions ran on a
subscription plan. No billing statement was consulted, no charge record exists
in hand, and this entry does not claim one. "This turn cost $20.70" means "this
turn consumed tokens worth $20.70 at list price".

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

1. **The pricing model reproduces the provider's own cost**, ≥90% of turns to
   within 0.1% — and the 5-minute-TTL rate reproduces **none** of them. The
   second half is the load-bearing one: a model that fitted both would identify
   no TTL at all.
2. **Mode A**: context handling ≥90% of session cost; ≥200 cached tokens read
   per token produced; and at least one turn provably re-read the context >10×
   over, derived from the window size.
3. **Mode B**: the peak turn follows a >1h gap, rewrites ≥900k tokens, costs
   >$20 with >90% of it in the rewrite line, runs >10× the session median — and
   a second session pays its own rewrite within 2 minutes.
4. **The limits of the causal claim**: asserts the aggregate association *and*
   both counterexamples — that long gaps sometimes rewrite nothing and short
   gaps sometimes rewrite everything. An oracle that pins what the entry
   **cannot** conclude, so §3 cannot quietly grow a stronger claim later.

**`simulate.py` — the counterfactual**, prominently labelled, five oracles
including that both arms produce identical output (so the defense cannot look
good by doing less work).

**Regression tests:**

```bash
python3 test_defense.py            # or, if pytest is installed:
python3 -m pytest test_defense.py -q
```

18 tests, ~0.05s. Seven pin the proposed defense — that it fires exactly at its
threshold, respects a configured one, rejects a nonsensical window, reduces
spend, does **not** change what was produced, is inert when never reached, and
is deterministic. Eleven pin the evidence files against the exact figures quoted
above, so editing a number in this README without editing the traces turns them
red.

One of those tests exists because of a bug in this entry's own simulation.
`test_defense_is_inert_when_the_watermark_is_never_reached` pins a case that
shipped broken in the first draft: the invented workload topped out at 430k
tokens against a 500k watermark, so the "defended" arm never fired and was
byte-identical to the marathon. The comparison was vacuous and the oracle caught
it — which is the argument for oracles that assert a *difference* rather than
printing two numbers for a human to eyeball.

## 5. Discrimination and defense

### Diagnosing it, in the order that costs least

1. **Decompose the bill before theorising about it.** Split cost into
   input / cache-read / cache-write / output. Every useful conclusion in this
   entry came from that one table. If output is a single-digit percentage, no
   amount of prompt tuning or effort-dialling will help, and you can stop
   considering it.
2. **Sort turns by cost and look at the gap *before* each one.** Wakeup spikes
   have a signature: enormous `cache_creation`, ordinary `output`, a long idle
   gap. They are unmissable once you look and invisible until you do.
3. **Count sessions, not turns.** The rewrite is per session. Two idle marathon
   sessions cost twice as much to wake as one, for zero extra work — as
   sessions B and C did, 81 seconds apart.
4. **Only then look at the model's behaviour.** "It's thinking too much" is the
   hypothesis of last resort. It was the hypothesis of first resort.

### The defense

Two operational rules, and one thing to build.

**One task, one session.** A session's context is a monotonically growing
liability, and it does not stop being one when you stop typing. Finish a task,
start a new session for the next one.

**Never hand a stray task to an idle marathon session.** It is the most
expensive place to do small work. "While I'm here, quickly…" in a
near-full, hours-idle session costs the full rewrite before the request is even
read — $19.50, in the case above, to deliver $0.15 of output.

**And the thing to build — a context watermark:**

```python
def should_retire_session(context_tokens, window, threshold=0.50):
    """Retire the session rather than hand it the next task."""
    return context_tokens >= window * threshold
```

That is the whole defense. It needs no knowledge of what the session is doing,
and — this is the part that stings — it needs no new instrumentation, because
**the CLI already reports `contextWindow` on every single turn.** Owlery stores
it. Nothing reads it.

`test_defense.py` pins that it fires exactly at its threshold, that it does not
fire below, that it reduces spend on identical work, and that it does not
achieve that by producing less.

### What Owlery actually does — reported as found

**Owlery has no context-level visibility of any kind, and its design actively
encourages the sessions that make that expensive.** Both halves are still true
as of 2026-07-20. This is the entry's own system, and writing it up as solved
would have been easy and false.

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

The data to fix it is already in the building. Owlery writes a `turn_usage` row
per turn carrying `cache_read_tokens`, `cache_creation_tokens`, and — inside the
stored `model_usage` blob — the model's `contextWindow`. It is all there, on
every turn, already persisted. What exists on top of it is a usage API that
aggregates **backwards** over a time window: how much was spent, after it was
spent. There is no forward-looking signal — no per-session context level, no
watermark, no warning, and nothing in the UI that would let an operator
distinguish a fresh session from one carrying 900k tokens of history.

The proposal in this section — surface the level, warn at a threshold — has been
written down and **not scheduled**. It is reported here as a known defect with a
known fix, not as work completed.

> **The lesson generalizes past agents.** When a discount is large enough, it
> stops being a saving and becomes a *blind spot*. Prompt caching cut the price
> of a re-sent context by 90%, which is exactly why a 220-to-1 read amplification
> could run for four hours without anything looking wrong. The per-unit number
> got better; the total got worse; and only the per-unit number was on screen.
> **Instrument the quantity that scales, not the one that got cheaper.**

**Detection in production**: one gauge and one counter. Gauge — context tokens
as a fraction of the window, per live session. Counter — turns whose
`cache_creation` exceeds some fraction of their total context. The first tells
you which sessions are expensive to keep; the second tells you when you paid to
put one back. Neither needs to know what the agent is doing.

---

## 中文摘要

**现象**:什么都没坏——这正是问题所在。agent 一切正常:turn 正常完成、工具正常调用、答案正常返回、测试正常通过。没有可以 grep 的错误、没有失败的 turn、没有延迟异常、没有任何告警。唯一的症状是账面上一个与工作量明显不相称的数字,而等你去看的时候,产生它的会话已经是几天前的事了。真正去看时,形状是反直觉的:**最贵的 turn 不是模型思考最多的那些**——A 会话整场生成了 197,896 个输出 token,这部分只占成本的 **9.2%**;**最贵的 turn 也不是最长的那些**——B 会话最贵的一轮只跑了 **131 秒**、只产出 **3,080 个 token**,却花掉 **$20.70**,是该会话中位 turn 的 12 倍;而且两个做着完全不同工作的会话会在同一瞬间(相隔 81 秒)一起飙升,原因**不在这两个会话中的任何一个**。让这一切隐形的,是那个最自然的心智模型:*成本随付出的努力走*。它不随。**成本几乎完全随上下文长度走,而与其中发生了多少思考基本无关。**

**根因(两种模式,同一个架构)**:模型是无状态的,而一个 agentic turn 是一个循环——harness 发送全部上下文,模型调用一次工具,harness 追加结果后**再次发送全部上下文**。一个包含十次工具调用的 turn,就是十一次全量上下文请求。缓存让这件事变得可负担,但不是免费:命中的前缀按输入价的 0.1× 计费。这个九折优惠正是没人察觉的原因——**单价**塌缩了两个数量级,于是**总额**可以一路攀升而始终看起来不像出了问题。
- **模式 A「热重读」(07-09 诊断)**:一个近满仓的马拉松会话,20 个 turn、每轮约十次工具调用,跑了近四小时。账本记录:cache-read **43,715,956** token($21.86,40.6%)、cache-write 2,685,449 token($26.85,49.9%)、输出 197,896 token($4.95,**9.2%**)、未缓存输入 $0.19,合计 **$53.85**。读了 4370 万缓存 token,只为产出 19.7 万输出 token,比值 **220.9 : 1**;上下文搬运占账单 **90.8%**,模型真正说出口的一切只占 9.2%。**先走的弯路**:最初怀疑是模型想得太多,该调低 effort 或压缩提示词——输出那一列直接否掉了它:在 9.2% 的占比下,哪怕让模型**彻底闭嘴**,省下的也不到账单的十分之一。单轮最差的一次读了 **11,289,438** 个缓存 token,而上下文窗口是 1,000,000 token,单次请求读不到超过窗口容量的缓存,所以那一个 turn **至少发生了 11.3 次模型调用**——这是由窗口大小推出的**下界**,不是计数(账本把整个 turn 聚合成一行,看不见单次调用),这也是「每轮约十次工具调用」这个说法所能取的最诚实形态。
- **模式 B「冷重写 / 唤醒税」(07-15 诊断)**:缓存有 TTL。失效后,下一个 turn 不是**重读**上下文,而是把它**重写**回去;而写入不是 0.1× 输入价,是 **2×**。同样的 token,仅仅因为时间流逝,价格摆动了 **20 倍**。B 会话(07-11 开启,07-15 仍活着)反复撞上这一点,最惨的一次:07-15 06:06:34,此前闲置 **4.1 小时**,cache-**write 974,755** token(窗口 1,000,000)、cache-read 1,049,591、输出仅 3,080、耗时 131 秒、**供应商报告成本 $20.6988**,其中重写占 **$19.50(94.2%)**,模型真正的输出值 $0.15。这些工作它先前已经做过了,它是在付钱**把它们放回去**。更糟的是重写是**按会话**计的:B 会话那笔 $20.70 之后 **81 秒**,**C 会话**——不同会话、不同任务、只是恰好一起闲置——为重写自己的 175,728 token 付了 **$3.8608**。**离开键盘一次,交两份税**;共同的成因既不是 B 也不是 C,而是时钟。仅 07-15 一天,B 会话在五个 turn 上付出 **$56.80** 的写入费。
- **两种模式其实是同一个 bug**:A 是**使用**大上下文的代价,B 是**保有**大上下文的代价。两者都由上下文长度而非实际工作量决定,且都不会出现在 agent 平台通常展示的任何指标里(轮次、延迟、错误、生成 token 数)。真正能预测账单的那个量,恰恰是没人显示的那个:**这个会话正背着多少上下文。**

**证据等级(两级,严格不混用)**:计费重建是 `trace replay`——`repro.py` 回放三个真实会话共 113 条脱敏的逐轮用量记录。**关键一步是计价模型经过验证而非假定**:用公开列表价与 token 计数重算每轮成本,再与 CLI 自己报告的成本比对,在 **2.00×**(**1 小时** TTL 的写入价)下 **99 轮中 95 轮**误差 <0.1%,而在 1.25×(5 分钟 TTL)下**一轮都对不上**。由此得到两件事:其一,本条目中每个金额都是对**供应商报告数值的分解**而非估算;其二,**在用的是 1 小时 TTL,这一点由计费算术确立,而非靠谁的记忆**——这很重要,因为模式 B 的机制正建立在该 TTL 之上。**有四轮对不上,它们被保留在 trace 里**,其中最离奇的一条是一个**所有 token 计数均为零、却被计费 $9.8231** 的错误轮次,我不知道那是什么。反事实部分是 `mechanism simulation`:账本记录了马拉松会话花了多少,却无法记录**同样的工作在更好的会话卫生下会花多少**,因为那次运行从未发生;`simulate.py` 用假时钟与假 TTL 缓存跑两条臂(16 个任务、两边输出完全相同),报出 **1.84×**——**这个数字是我所选参数的性质**,换参数就变,它只用来展示机制的**形状**,并被隔离在独立文件、独立横幅、独立评级之下;**本条目没有任何一张表把模拟数字与实测数字混在一起。**

**账本纠正了当时笔记的四处错误(如实记录而非悄悄改掉)**:(a) 笔记称「4370 万 cache-read ≈ $54」——token 数没错,但 **$53.85 是整场会话的总额**,cache-read 这一行本身只有 **$21.86**,笔记把行项当成了总额;(b) B 会话一生累计笔记记为 **$212**,账本是 **$235.7484**;(c) 「当天四次唤醒税约 $50」实为**五**个重写 ≥400k token 的 turn、写入费共 **$56.80**;(d) 「1,042 条消息」**无法核实**——B 会话在 `sessions` 表中的行及其全部消息此后已被删除,只有用量账本幸存,「近 1M 上下文」改由另一条证据佐证:那次针对 1,000,000 token 窗口的 974,755 token 重写。此外还要纠正本条目自身的措辞:07-09 的笔记说钱花在**重读**上,方向是对的(上下文搬运 90.8% vs 生成 9.2%),但在 A 会话内部,**写入**那一行($26.85)其实比**读取**那一行($21.86)更大——「是重读」是好记的版本,「是上下文,两个方向都是」才是真的。

**因果claim到哪里为止(最重要的一节)**:最顺手的句子是「会话闲置超过 TTL,所以缓存失效,所以下一轮重写了它」。trace **在总体上支持**它、**在单轮上证伪**它,而 `repro.py` 的 oracle 4 把**两半都写成断言**,使文案无法漂移。仅统计 claude-code 轮次:>1h 间隔的 20 个 turn 重写占比中位数 **28.1%**,≤1h 间隔的 75 个 turn 中位数 **1.2%**——中位数相差 **24 倍**,真实且显著。**但**:**不充分**——有些间隔超过一小时的 turn 只重写了 **0.2%**;**不必要**——有些间隔仅几分钟的 turn 重写了 **93%**,B 会话中甚至有连续三个 turn 在七分钟内各自重写约 37 万 token,TTL 解释不了,而我**不知道**是什么解释得了(缓存前缀失效是显而易见的候选,但我没有证据)。所以:**「闲置超 TTL」对重写尖峰既不充分也不必要**。本条目主张关联、机制与算术,**不主张**任何单次尖峰由时钟造成——严格说来,**包括那笔 $20.70**;关于它已确立的是:它跟在 4.1 小时的间隔之后、它重写了 974,755 token、该重写花了 $19.50,而第一项与第二项之间的因果是**推断**,并在此标注为推断。还有一个差点炮制出假结果的混淆项:**codex** 后端在每一轮都把 `cache_creation_tokens` 报成 0——不是因为没有写入,而是因为它不报告该字段;把这些轮次算进去,会得到一批「长间隔却毫无重写」的漂亮样本,而那纯属仪表假象,oracle 4 已将其排除。

**防御**:两条操作纪律加一件该建的东西。**一任务一会话**——会话的上下文是单调增长的负债,而且不会因为你停止打字就不再是负债。**绝不把顺手的小任务丢给闲置的马拉松会话**——那是做小事最贵的地方,「反正我在这儿,顺便……」在一个近满仓、已闲置数小时的会话里,请求还没被读到就已经付掉了全额重写:上例中是用 **$19.50** 换 **$0.15** 的输出。**该建的东西是上下文水位线**:`context_tokens >= window * threshold` 就退休该会话、另开新的。整个防御就这一行,它不需要知道会话在做什么,而扎心的是**它也不需要任何新埋点——CLI 本来就在每一轮报告 `contextWindow`,Owlery 也存了下来,只是没有任何代码去读它。**

**Owlery 的真实情况——如实报告,截至 2026-07-20 两处仍然存在**:**Owlery 没有任何形式的上下文水位可见性,而它的设计还在主动鼓励那些让这件事变贵的会话。** 平台按设计把工作**推入**长驻会话:消息桥的一个聊天以**粘性会话**绑定到 agent(该聊天的每条消息永远落进同一个会话);**委派回复**作为后续轮次注入**调用方**会话;**后台任务结果**同样注入发起它的会话。这些**每一个都是好功能**,但合在一起意味着:一个有用的 Owlery 会话,其默认生命周期是**永远**,而没有任何地方告诉操作者这正在花多少钱。修复所需的数据其实早就在楼里——Owlery 每轮写一条 `turn_usage`,带着 `cache_read_tokens`、`cache_creation_tokens`,以及存在 `model_usage` 里的 `contextWindow`,全都在,每一轮都在,已经持久化了。建在它上面的,是一个**向后**按时间窗聚合的用量 API:花了多少,在花完之后。**没有任何前瞻信号**——没有按会话的上下文水位、没有水位线、没有提醒,UI 里也没有任何东西能让操作者区分一个全新会话和一个背着 90 万 token 历史的会话。本节这个提案(显示水位、到阈值提醒)**已写下但未立项**,在此作为「已知缺陷 + 已知修法」报告,而**不是**作为已完成的工作。

**通用教训**:**当一个折扣大到一定程度,它就不再是节省,而变成盲区。** 缓存把重发上下文的价格砍掉 90%,而这恰恰是「220 : 1 的读放大」能连跑四小时而毫无异样的原因——**单位数字变好了,总额变坏了,而屏幕上只有单位数字。要给会规模化的那个量装仪表,而不是给变便宜的那个量装。** 生产检测:一个 gauge(每个活跃会话的上下文 token 占窗口比例)加一个 counter(`cache_creation` 超过自身总上下文某一比例的轮次数)——前者告诉你哪些会话「留着很贵」,后者告诉你何时付钱把一个会话放了回去;两者都不需要知道 agent 在做什么。
