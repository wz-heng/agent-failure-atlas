# A string classifier falsified by real samples: usage-limit detection

> **Evidence level: `live reproduction`** — captured from real CLI processes
> answering real HTTP 429 envelopes. The samples in [`evidence/`](evidence/)
> are those streams, redacted. Nothing here is hand-written to fit the
> conclusion.

| | |
|---|---|
| **Systems** | Claude Code CLI `2.1.209`, Codex CLI `0.142.5` |
| **Platform** | macOS 15 (darwin 25.x), Python 3.12 |
| **Samples captured** | 2026-07-14 |
| **Impact** | Agent sessions die unattended for hours; or suspend for hours on a blip that would have cleared in seconds |
| **Repro** | `python3 repro.py` — offline, no account, <1s |

---

## 1. Symptom

An agent runtime has to decide what a failed turn means. Two of the possible
answers are hours apart in consequence:

- **The user's own quota window is exhausted.** Retrying is pure waste — the
  window will not reopen for hours. Correct action: *park* the turn, persist
  it, wake at the reset epoch, resume once.
- **The provider is throttling server-side.** Correct action: *retry* with
  backoff on a seconds scale.

Both arrive as **HTTP 429**. Both render prose containing "rate limit". Get the
mapping backwards in either direction and you get one of two failures:

- **False park** — a two-second blip suspends the session for five hours.
  Scheduled runs miss their windows, delegation chains stall, and a phone user
  sees "auto-resuming at 22:30" for a problem that fixed itself before they
  read the message.
- **False retry** — the runtime hammers an exhausted quota, then surfaces a
  generic error. Every in-flight task sits dead until a human notices. On a
  long working day this repeats, and the lost hours are pure waste: the work
  was going to continue anyway; the only missing input was "wait until HH:MM,
  then press go".

The symptom that makes this entry worth writing is that **the two cases look
nearly identical in text and are opposite in disposition.**

## 2. Root cause — including the wrong path taken first

### The design that was written first

The original plan specified detection by substring, mirroring the two
classifiers already in the codebase (auth-rejection and transient-error, both
pattern sets). It proposed a third pattern set, `usage_limit_patterns`, and
asserted:

> The classifiers stay mutually exclusive by construction. Today the bare
> "rate limit" / "429" / "quota" tokens appear in *neither* pattern set
> precisely so the user's-limit message falls through to "surface as-is"; this
> work claims those messages for the new classifier instead.

"Mutually exclusive **by construction**" is the load-bearing error. Disjointness
was assumed as a property the author could establish by writing the pattern
sets carefully. It is not — it is a property of *the vendor's message space*,
and no amount of care on the consumer side can create it if the vendor's two
message classes overlap.

The same plan did one thing right, and it is the reason this entry exists
rather than a production incident:

> **Ground truth first**: no local transcript currently holds a real sample of
> either CLI's limit message (searched before writing this), so the first
> implementation step is to capture live samples from both backends — and
> derive patterns from those, not from folklore.

### What the samples showed

Four samples were captured — for each CLI, both a user-limit failure and a
server-side 429. The claude pair falsified the design outright:

| | user's own 5-hour limit | server-side throttle |
|---|---|---|
| HTTP status | 429 | 429 |
| rendered prose | `You've hit your session limit · resets 10:22pm (Asia/Shanghai)` | `API Error: Server is temporarily limiting requests (not your usage limit) · You have exceeded your account's rate limit.` |
| correct action | park for hours | retry in seconds |

Three things go wrong at once:

1. **The status code carries no information.** It is 429 on both.
2. **The vocabulary is inverted.** "rate limit" and "usage limit" appear in the
   *throttle* message and are **absent from the real limit message**, which
   says "session limit". A pattern set built from the obvious vocabulary fires
   on exactly the wrong sample.
3. **The most specific-looking phrase is a negation.** The throttle message
   contains "usage limit" only inside `(not your usage limit)`. Substring
   matching cannot see negation, so the phrase an engineer would trust *most*
   is the one that misleads *hardest*.

`repro.py` proves this is not a matter of choosing better patterns. A substring
classifier answers "park" iff one of its patterns occurs in the text; to be
right on both claude samples it needs a pattern occurring in the user-limit text
but not in the throttle text. Over the rate-limit vocabulary, **that set is
empty** — every such token present in the user-limit sample is also present in
the throttle sample. The only strings that *would* separate them are incidental
localized prose (`session limit`, `resets 10:22pm`) — rendered output that
changes with locale, plan tier, and release.

### The second-order cause

Why was the discriminator not used in the first place? Because the parser was
**throwing it away**. Claude's stream carries a dedicated `rate_limit_event`
record with the structured limit state; the parser dropped it on the floor as
having "nothing to surface" in the UI. A record with no rendering was treated as
a record with no value, so detection had nothing left to key on but prose. The
fix had to start by latching a record that was already arriving.

## 3. Evidence

`live reproduction`. Each sample was produced by pointing the real CLI at a
local HTTP upstream returning a genuine 429 envelope and running the CLI's real
spawn → stream-json path. The CLI processes and their parsing are real; only the
upstream is local. No quota was spent — the model is never reached.

| file | class | key structure |
|---|---|---|
| [`evidence/claude_user_limit_5h.jsonl`](evidence/claude_user_limit_5h.jsonl) | user's own limit | `rate_limit_event` → `status: rejected`, `rateLimitType: five_hour`, `resetsAt: 1784038967` |
| [`evidence/claude_server_429.jsonl`](evidence/claude_server_429.jsonl) | server throttle | `rate_limit_event` → `status: rejected`, **no `rateLimitType`** |
| [`evidence/codex_user_limit.jsonl`](evidence/codex_user_limit.jsonl) | user's own limit | `error.message` contains `You've hit your usage limit` |
| [`evidence/codex_server_429.jsonl`](evidence/codex_server_429.jsonl) | server throttle | `error.message` = `exceeded retry limit, last status: 429 Too Many Requests` |

Versions: `claude-code 2.1.209`, `codex-cli 0.142.5`. Captured 2026-07-14.
Redaction procedure and exactly what was removed: [`evidence/README.md`](evidence/README.md).

**Known limits of this evidence.** The samples are single captures from one
account on one plan tier, in one locale (`Asia/Shanghai`), on one date. They
establish that the two classes overlapped in these versions — which is enough to
falsify "disjoint by construction". They do **not** establish that the overlap
is exhaustive, that other plan tiers or locales render the same strings, or that
these versions still behave this way. That is the point of pinning versions: the
claim is historical and evidenced, not perpetual.

**What is *not* claimed:** that the structural fields are a documented, stable
vendor contract. They are not documented as such. They are a better bet than
prose — see below — not a guarantee.

## 4. Minimal case

```bash
cd entries/string-classifier-falsified-by-real-samples
python3 repro.py        # exit 0 = every oracle held
```

Offline, no account, no network, no API spend, well under a second. Standard
library only; imports nothing from the system it came from — both classifiers
are reimplemented in the file from the shipped logic.

Three machine-checked oracles:

1. **Falsification** — the pattern classifier's verdicts on all four samples,
   asserted exactly. It answers "park" to every one, including both throttles:
   a discriminator with zero discriminating power.
2. **Impossibility** — the set-difference proof above, computed on the real
   texts. Fails loudly if a separating token ever appears, so the entry cannot
   quietly outlive its own evidence.
3. **Defense** — the structural classifier is correct on all four, and recovers
   the reset epoch verbatim.

Regression tests for the defense:

```bash
python3 test_defense.py            # or: python3 -m pytest test_defense.py -q
```

10 tests: the four captured samples, disjointness, epoch provenance, and the
three shapes not represented in the corpus (rejection with no window claim,
`allowed_warning` on a *successful* turn, and a hit whose epoch is missing).

## 5. Discrimination and defense

**The discriminator is whether the CLI names the exhausted window.**

```python
if rate_limit_info.get("status") == "rejected":
    window = rate_limit_info.get("rateLimitType")   # "five_hour", ...
    if isinstance(window, str) and window:
        return "park"                                # + resetsAt epoch
    return "retry"                                   # rejected, claims no window
```

A user-limit rejection names the window it exhausted and carries the epoch it
reopens. A server-side throttle is rejected claiming no window at all. Same
status code, same prose vocabulary, different *shape*.

**Honest caveat: the codex half is still a string match.** Codex puts no
structured limit state on `exec --json` stdout, so classification keys on the
phrase `you've hit your usage limit`. Calling that "structural" would be
dressing up a substring check. What changed is not the *form* of the check but
its *warrant*: there is a captured sample of the opposing class proving the
throttle message does not contain the phrase. The lesson is not "never match
strings" — it is:

> **Disjointness is an empirical claim about the vendor's output, not a
> property you can write into your own code. It requires a captured sample of
> every class you are separating — especially the class you expect *not* to
> match.**

Three defenses follow from that, all of them in the shipped fix:

1. **Key on fields the vendor emits for machines, not strings it renders for
   humans.** The reset time comes from the `resetsAt` epoch, never parsed back
   out of `resets 10:22pm (Asia/Shanghai)`. That prose is *rendered from* the
   epoch; parsing it downgrades reliable data into a fragile localized string.
2. **Keep the classifier pure and stream-only.** Detection reads the turn's
   events and nothing else — no disk, no config. That is what makes the
   disjointness provable on captured fixtures alone, with no hidden state. In
   the shipped fix, the one classifier that needed I/O (codex reads its reset
   epoch from a rollout file) was split into a separate hook that may only
   *supply a missing epoch* — it can never revisit the verdict.
3. **Pin the negative samples in regression tests.** The throttle samples are as
   important as the limit samples, and cheaper to forget. `test_defense.py`
   asserts both directions on every capture.

**Detection in production**: a classifier that answers "park" to a rising share
of failed turns is the observable symptom of this bug. Counting park verdicts
against actual multi-hour waits catches a false-park regression without waiting
for a user to report a stalled session.

---

## 中文摘要

**现象**:agent 运行时必须区分两类失败——「用户自己的配额窗口耗尽」(该停下、等到窗口重置再续跑,量级是小时)和「服务端限流」(该秒级退避重试)。两者都是 HTTP 429,渲染文案都含 "rate limit"。判错任一方向:要么两秒的抖动让会话空挂五小时,要么真实限额被反复冲撞、所有在途任务无人值守地死掉。

**根因(含误诊路径)**:最初的设计用字符串 pattern 匹配做判别,并断言「各分类器按构造互斥」——这句是致命错误。互斥性是*供应商消息空间*的性质,不是消费者写代码能创造的性质。所幸设计里坚持了「先拿 ground truth」:抓四份真实样本后,claude 那一对直接证伪了设计——真实限额说的是 "session limit",而 "rate limit"、"usage limit" 这些词恰恰只出现在**服务端限流**那条里,且 "usage limit" 只以否定形式出现:`(not your usage limit)`。子串匹配看不见否定,最像的短语误导最狠。`repro.py` 进一步证明这不是「换个 pattern 就行」:限额词表中出现在真实限额样本里的词,**无一不同时出现在限流样本里**,差集为空,任何基于该词表的子串分类器都分不开这两条。深层原因是解析器把带判别信息的 `rate_limit_event` 当成「没法渲染所以没价值」直接丢弃了。

**证据等级**:`live reproduction`。真实 CLI 进程(claude-code 2.1.209 / codex-cli 0.142.5)对着返回真 429 信封的本地上游跑真实 spawn → stream-json 路径,2026-07-14 捕获,未消耗任何额度。

**防御**:判别键改为「CLI 有没有指名被耗尽的窗口」——真实限额带 `rateLimitType`(如 `five_hour`)和重置纪元 `resetsAt`,服务端限流则 `rejected` 但不指名任何窗口。重置时间只取结构化纪元,绝不回头解析 `resets 10:22pm` 这种本地化渲染文案。诚实地讲,codex 那一半仍是字符串匹配——变的不是检查的*形式*而是它的*凭据*:存在一份对立类别的真实样本,证明限流文案不含该短语。真正的教训是:**互斥性是关于供应商输出的经验主张,必须为每个待区分的类别都捕获样本,尤其是你预期「不该匹配」的那个类别。**
