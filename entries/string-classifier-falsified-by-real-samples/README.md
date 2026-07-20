# A usage-limit classifier falsified by real samples

> **Evidence level: `trace replay`** — four redacted real CLI event streams,
> replayed to reproduce how the consumer-side classifier fails. The vendor's
> behaviour is proven by the versioned traces in [`evidence/`](evidence/), not
> re-elicited on demand: this entry ships no runnable capture path, so it does
> not meet this atlas's bar for `live reproduction`. Nothing in the traces was
> hand-written to fit the conclusion.

| | |
|---|---|
| **Hit in** | Owlery — a personal agent platform driving the Claude Code and Codex CLIs (private repository) |
| **Systems** | Claude Code CLI `2.1.209`, Codex CLI `0.142.5` |
| **Platform** | macOS 26.3 (Darwin 25.3.0), Python 3.12 |
| **Traces captured** | 2026-07-14 |
| **Impact** | Agent sessions die unattended for hours; or suspend for hours on a blip that clears in seconds |
| **Repro** | `python3 repro.py` — offline, no account, <1s |

---

## 1. Symptom

An agent runtime has to decide what a failed turn means. Three dispositions
matter here, and two of them are hours apart in consequence:

- **park** — the user's own quota window is exhausted. Retrying is waste; the
  window will not reopen for hours. Persist the turn, wake at the reset epoch,
  resume once.
- **retry** — the provider is throttling or wobbling server-side. Back off on a
  seconds scale and re-send.
- **surface** — anything else. Report it and stop.

Get park and retry backwards in either direction and you get one of two
failures:

- **False park** — a two-second blip suspends the session for five hours.
  Scheduled runs miss their windows, delegation chains stall, and a phone user
  sees "auto-resuming at 22:30" for a problem that fixed itself before they read
  the message.
- **False retry** — the runtime hammers an exhausted quota, then surfaces a
  generic error. Every in-flight task sits dead until a human notices. On a long
  working day this repeats, and the lost hours are pure waste: the work was
  going to continue anyway; the only missing input was "wait until HH:MM, then
  press go".

What makes this worth writing up is how little the obvious signals help. For
**claude**, the user's own limit and a server-side throttle both arrive as HTTP
429, and the throttle's prose contains *more* rate-limit vocabulary than the
real limit's does. For **codex**, the real usage limit carries no 429 in its
stream at all — so a status-code rule fails in the opposite direction too.

## 2. Root cause — including the wrong path taken first

### The assumption that was wrong

Owlery already had two text-matching classifiers (auth-rejection, transient
error). The first cut of the usage-limit plan proposed a third pattern set in
the same style, and asserted:

> The classifiers stay mutually exclusive by construction. Today the bare
> "rate limit" / "429" / "quota" tokens appear in *neither* pattern set
> precisely so the user's-limit message falls through to "surface as-is"; this
> work claims those messages for the new classifier instead.

*(`docs/plans/limit-auto-resume.md` @ `45fb759`, 2026-07-14)*

"Mutually exclusive **by construction**" is the load-bearing error. Disjointness
was treated as a property the author could guarantee by writing the pattern sets
carefully. It is not — it is a property of *the vendor's message space*, and no
care on the consumer side can create it if the vendor's two message classes
overlap.

The same plan did one thing right, and it is the only reason this is an entry
rather than a production incident:

> **Ground truth first**: no local transcript currently holds a real sample of
> either CLI's limit message (searched before writing this), so the first
> implementation step is to capture live samples from both backends — and derive
> patterns from those, not from folklore.

So the generic-vocabulary classifier was **proposed and abandoned, never
shipped**. `repro.py` reconstructs it to show what the proposal would have done;
it is labelled `ILLUSTRATIVE` there, and it is not a reimplementation of
anything that ran in production.

### What the traces showed

Four traces were captured (`528d45f`) — for each CLI, both a user-limit failure
and a server-side 429. The claude pair killed the proposal outright:

| | user's own 5-hour limit | server-side throttle |
|---|---|---|
| HTTP status | 429 | 429 |
| rendered prose | `You've hit your session limit · resets 10:22pm (Asia/Shanghai)` | `API Error: Server is temporarily limiting requests (not your usage limit) · You have exceeded your account's rate limit.` |
| correct disposition | park (hours) | retry (seconds) |

Three things go wrong at once:

1. **The status code carries no signal.** It is 429 on both sides of the claude
   pair — and *absent* from codex's real usage limit, so it is not even a
   reliable positive indicator.
2. **The generic vocabulary is inverted.** "rate limit" and "usage limit" appear
   in the *throttle* message and are **absent from the real limit message**,
   which says "session limit". A pattern set built from the obvious tokens fires
   on exactly the wrong sample.
3. **The most specific-looking phrase is a negation.** The throttle message
   contains "usage limit" only inside `(not your usage limit)`. Substring
   matching cannot see negation, so the phrase an engineer would trust *most*
   is the one that misleads *hardest*.

### The second-order cause

Why was a structured discriminator not used from the start? Because the parser
was **throwing it away**. Claude's stream carries a dedicated `rate_limit_event`
record with the structured limit state; Owlery's parser dropped it as having
"nothing to surface" in the UI. A record with no rendering was treated as a
record with no value, so detection had nothing left but prose. The fix had to
begin by latching a record that was already arriving.

### What this entry does *not* claim

An earlier revision of this entry argued that **no** substring classifier could
separate the claude pair, and shipped a "proof" to that effect. That was wrong,
and the way it was wrong is instructive: the vocabulary in the proof had been
drawn from rate-limit terminology, which quietly excluded `session limit` — a
string that *does* occur in the user-limit trace and *not* in the throttle
trace. A reviewer found it in minutes. Two substrings, `session limit` and
`you've hit your usage limit`, classify all four traces correctly;
`repro.py` oracle 3 now demonstrates exactly that, on purpose.

So the correct claim is narrower:

> **The HTTP status code and generic rate-limit vocabulary are not reliable
> discriminators.** Prose matching *can* separate this corpus — the argument
> against it is fragility, not impossibility.

Those separating strings are localized, rendered output. `session limit` and
`resets 10:22pm (Asia/Shanghai)` change with locale, plan tier, and release, and
no vendor promises to keep them stable. Keying on them is a bet that renders
differently for a user in another timezone.

**The meta-lesson**, since this atlas exists to be checkable: an entry about
overreaching from limited samples nearly shipped an overreach from limited
samples. The correction is in `repro.py` as a permanent oracle, not just in
prose.

## 3. Evidence

`trace replay`. Each trace was produced by pointing the real CLI at a local HTTP
upstream that returned a genuine 429 envelope, then running the CLI's own
spawn → stream-json path. The CLI binaries, their argument handling, their retry
behaviour and their output encoding are all real; only the upstream is local,
which is why no quota was spent — the model is never reached. This entry ships
the resulting traces, not the capture rig, which is why it is graded
`trace replay` rather than `live reproduction`.

| file | class | key structure |
|---|---|---|
| [`evidence/claude_user_limit_5h.jsonl`](evidence/claude_user_limit_5h.jsonl) | user's own limit | `rate_limit_event` → `status: rejected`, `rateLimitType: five_hour`, `resetsAt: 1784038967` |
| [`evidence/claude_server_429.jsonl`](evidence/claude_server_429.jsonl) | server throttle | `rate_limit_event` → `status: rejected`, **no `rateLimitType`** |
| [`evidence/codex_user_limit.jsonl`](evidence/codex_user_limit.jsonl) | user's own limit | `error.message` contains `You've hit your usage limit` |
| [`evidence/codex_server_429.jsonl`](evidence/codex_server_429.jsonl) | server throttle | `error.message` = `exceeded retry limit, last status: 429 Too Many Requests` |

Versions: `claude-code 2.1.209`, `codex-cli 0.142.5`. Captured 2026-07-14.
Redaction procedure and exactly what was removed:
[`evidence/README.md`](evidence/README.md).

**Provenance in the source repository.** The history is the argument, so it is
cited rather than summarised. All paths are in Owlery (private):

| commit | date | what |
|---|---|---|
| `45fb759` | 2026-07-14 10:14 | `docs/plans/limit-auto-resume.md` — first cut: a `usage_limit_patterns` string set, "mutually exclusive by construction", but ground-truth-first mandated |
| `528d45f` | 2026-07-14 19:27 | `tests/fixtures/limit_*.jsonl` — the four traces captured; the falsification |
| `cb3a1af` | 2026-07-14 19:31 | plan rewritten: "detection is structural, not textual" |
| `3f08320` | 2026-07-14 20:09 | detection split into a pure stream-only classifier plus a separate I/O epoch lookup |
| `858924d` | 2026-07-16 14:00 | `server/harness/{claude_code,codex}.py` — the shipped classifiers and `tests/test_limit_auto_resume.py`, which pins the disposition matrix this entry reproduces |

Four minutes separate the falsification from the plan rewrite. That is what
having the samples buys.

**Known limits of this evidence.** The traces are single captures from one
account on one plan tier, in one locale (`Asia/Shanghai`), on one date. They
establish that the two classes overlapped in these versions — enough to falsify
"disjoint by construction". They do **not** establish that the overlap is
exhaustive, that other plan tiers or locales render the same strings, or that
these versions still behave this way. That is the point of pinning versions: the
claim is historical and evidenced, not perpetual.

**What is *not* claimed:** that the structured fields are a documented, stable
vendor contract. They are not documented as such. They are a better bet than
prose — see below — not a guarantee.

## 4. Minimal case

```bash
cd entries/string-classifier-falsified-by-real-samples
python3 repro.py        # exit 0 = every oracle held
```

Offline, no account, no network, no API spend, well under a second. Standard
library only; imports nothing from Owlery — the classifiers are transcribed into
the file.

Four machine-checked oracles:

1. **The status code is not a discriminator** — the exact per-trace presence of
   `429`, asserted. Present on both sides of the claude pair; absent from
   codex's real limit. Fails in both directions.
2. **One fixed generic vocabulary fails** — the illustrative classifier's
   verdicts on all four traces, asserted exactly. It answers "park" to every one,
   including both throttles. This is a counterexample against that vocabulary,
   not a claim about substring matching in general.
3. **Prose matching is possible but fragile** — the honesty oracle. It exhibits
   two hand-tuned substrings that classify all four traces *correctly*, so the
   entry cannot quietly regrow the impossibility claim it got wrong once.
4. **The shipped classifier** — correct on all four, including codex's
   surface-as-is case, and recovers the reset epoch verbatim.

Regression tests for the defense:

```bash
python3 test_defense.py            # or: python3 -m pytest test_defense.py -q
```

12 tests: the four traces, disjointness (with each disposition's predicate
computed *independently*, so it can actually fail), the exact
`(is_limit, is_transient)` matrix Owlery pins at `858924d`, epoch provenance,
and the shapes not represented in the corpus — rejection with no window claim,
`allowed_warning` on a *successful* turn, and a hit whose epoch is missing.

## 5. Discrimination and defense

The two backends do not offer the same footing, and the honest write-up says so
rather than claiming one clean rule.

**claude — a structured field.** The discriminator is whether the CLI *names the
exhausted window*:

```python
if rate_limit_info.get("status") == "rejected":
    window = rate_limit_info.get("rateLimitType")   # "five_hour", ...
    if isinstance(window, str) and window:
        return "park"                                # + the resetsAt epoch
    # rejected but claiming no window -> the throttle shape; fall through
```

A user-limit rejection names the window it exhausted and carries the epoch it
reopens. A server-side throttle is rejected claiming no window at all. Same
status code, same prose vocabulary, different *shape*. The reset time comes from
the `resetsAt` epoch and is never parsed back out of `resets 10:22pm
(Asia/Shanghai)` — that prose is *rendered from* the epoch, so parsing it
downgrades reliable data into a fragile localized string.

**codex — a string marker, plus structured data out of band.** Codex puts no
structured limit state on `exec --json` stdout, so there is no field to key on.
Classification matches the phrase `you've hit your usage limit`. Calling that
"structural" would be dressing up a substring check. What changed is not the
*form* of the check but its *warrant*: there is a captured trace of the opposing
class proving the throttle message does not contain the phrase. The epoch is
still not taken from prose — it is read as structured data from codex's rollout
file, by a separate hook that may only supply a missing epoch and can never
revisit the verdict.

Owlery's transient sets are worth reading for the same reason. They match the
throttle by its *specific* annotations — `"temporarily limiting requests"`,
`"not your usage limit"` — and deliberately contain no bare `"rate limit"`,
`"429"` or `"quota"`. `test_defense.py` pins that, because adding one would
collapse the disjointness argument silently.

The lesson generalizes past this bug:

> **Disjointness is an empirical claim about the vendor's output, not a property
> you can write into your own code. It requires a captured sample of every class
> you are separating — especially the class you expect *not* to match.**

Three defenses follow, all of them in the fix that shipped in Owlery:

1. **Prefer fields the vendor emits for machines over strings it renders for
   humans** — and where no such field exists, say so plainly instead of
   relabelling a string match as structural.
2. **Keep the classifier pure and stream-only.** Detection reads the turn's
   events and nothing else — no disk, no config. That is what makes disjointness
   provable on captured traces alone, with no hidden state. The one classifier
   that needed I/O was split into a separate hook (`3f08320`) that may only
   supply a missing epoch.
3. **Pin the negative traces in regression tests.** The throttle traces matter as
   much as the limit traces and are cheaper to forget. `test_defense.py` asserts
   both directions on every capture.

**Detection in production**: a classifier that answers "park" for a rising share
of failed turns is the observable symptom of this bug. Counting park verdicts
against actual multi-hour waits catches a false-park regression without waiting
for a user to report a stalled session.

---

## 中文摘要

**现象**:agent 运行时必须区分三类失败——「用户自己的配额窗口耗尽」(park:等窗口重置再续跑,量级是小时)、「服务端抖动/限流」(retry:秒级退避)、以及「其余」(surface:报错停下)。判错前两者任一方向:要么两秒的抖动让会话空挂五小时,要么真实限额被反复冲撞、所有在途任务无人值守地死掉。难点在于常规信号几乎不起作用:claude 的真实限额与服务端限流都是 HTTP 429,而且**限流那条的限额词汇比真实限额还多**;codex 的真实限额流里则压根没有 429,状态码规则在反方向同样失效。

**根因(含误诊路径)**:初版计划打算再加一个字符串 pattern 集,并断言「各分类器按构造互斥」——这句是致命错误。互斥性是*供应商消息空间*的性质,不是消费者写代码能创造的性质。所幸同一份计划坚持了「先拿 ground truth 再派生 pattern」,所以这个通用词方案**只是被提出、随即被推翻,从未上线**(repro.py 里的重建版明确标注为 ILLUSTRATIVE)。四份真实 trace 到手后,claude 那一对直接证伪了它:真实限额说的是 "session limit",而 "rate limit"、"usage limit" 恰恰只出现在**限流**那条里,且 "usage limit" 只以否定形式出现:`(not your usage limit)`。更深一层的原因是解析器把携带判别信息的 `rate_limit_event` 当成「没法渲染所以没价值」直接丢弃了。

**本条目不主张什么**:本条目的早期版本曾论证「任何子串判别器原理上都分不开这两条」,并附了一份「证明」。那是错的,且错法本身很有教益:证明所用词表取自限额术语,悄悄排除了 `session limit`——这个串只出现在限额 trace、不出现在限流 trace。复核者几分钟就找到了反例。因此正确的、收窄后的主张是:**HTTP 状态码与通用限额词汇不是可靠的判别依据;prose 匹配能分开这份语料,反对它的理由是脆弱性,不是不可能性。** 那些能分开的串都是本地化的渲染输出,随语言、套餐档位和版本变化。repro.py 的 oracle 3 现在专门把这个反例固化下来。顺带一提的元教训:一个讲「样本有限却过度推论」的条目,自己差点就过度推论了。

**证据等级**:`trace replay`(非 live——本条目只提供 trace,不提供可运行的捕获路径)。真实 CLI 进程(claude-code 2.1.209 / codex-cli 0.142.5)对着返回真 429 信封的本地上游跑真实 spawn → stream-json 路径,2026-07-14 捕获,未消耗任何额度。源仓库史实见上表五个 commit。

**防御(分后端,不假装统一)**:claude 用结构字段——判别键是「CLI 有没有指名被耗尽的窗口」(`rateLimitType` + 重置纪元 `resetsAt`),限流则 `rejected` 但不指名任何窗口。codex 没有可用的结构字段,仍是字符串 marker;变的不是检查的*形式*而是它的*凭据*:存在一份对立类别的真实 trace,证明限流文案不含该短语;纪元则改从 rollout 文件按结构读取,不解析文案。真正的教训是:**互斥性是关于供应商输出的经验主张,必须为每个待区分的类别都捕获样本,尤其是你预期「不该匹配」的那个类别。**
