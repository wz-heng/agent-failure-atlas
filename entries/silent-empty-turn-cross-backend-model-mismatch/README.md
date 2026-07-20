# The silent empty turn: a model name that belonged to the other backend

> **Evidence level: `trace replay`** — for the *consumer-side defect*.
> [`repro.py`](repro.py) replays real captured Codex CLI streams
> ([`evidence/`](evidence/)) through the classification logic Owlery ships, and
> reproduces a turn that is reported as a success while delivering nothing.
> The 2026-07-13 / 07-19 incidents themselves are **operator testimony**,
> labelled as such rather than graded — and I **tried to reproduce them and
> could not**. Read [§3](#3-evidence) before quoting either.

| | |
|---|---|
| **Hit in** | Owlery — a personal agent platform driving the Claude Code and Codex CLIs (private repository) |
| **Systems** | Codex CLI `0.142.5` and `0.144.6`, both captured; the consumer is Owlery's harness as of 2026-07-20 |
| **Platform** | macOS 26.3 (Darwin 25.3.0), Python 3.12 |
| **Incidents** | 2026-07-13 and 2026-07-19, single developer machine |
| **Traces captured** | 2026-07-20 |
| **Impact** | The agent goes "read but no reply". No error, no warning, no failed turn — nothing to search for, on either side. Days of intermittent misdiagnosis |
| **Repro** | `python3 repro.py` — offline, no account, ~1s |

---

## 1. Symptom

You send your agent a message. The turn runs for 11 to 30 seconds. Then it
finishes, and nothing comes back.

Not an error. Not an empty bubble. Not a spinner that never resolves. The
session is idle again, the turn is recorded as complete, and the transcript
simply has no reply in it. Every layer reports success:

- the CLI exits **0**;
- its event stream ends with a **terminal success** record;
- the runtime marks the turn done and unblocks the queue;
- the UI renders a completed turn with no assistant message.

There is nothing to grep for. There is no error string, no non-zero exit, no
stack trace, no failed-turn counter to look at. The only artifact of the failure
is an *absence*, and absences do not appear in logs.

This is what makes the class worth an entry. A crash tells you where to look. A
silent success tells you nothing at all, and the natural first hypothesis — "the
model chose not to reply" — is both plausible and completely wrong.

## 2. Root cause — three rings, and the wrong paths between them

> **`[operator testimony]` — the incident narrative in this section comes from
> the operator's own diagnostic notes, written at the time. No terminal
> transcript or event-stream capture from either day survives. What is
> independently checkable is stated in [§3](#3-evidence); what is *not*
> reproducible today is stated there too, and it is the more important half.**

The same symptom was diagnosed three times over six days, and had a different
cause each time. That is the point of the entry: the symptom is not a
fingerprint of any one bug. It is the fingerprint of *a whole class of
upstream failure arriving through a channel that cannot express failure*.

### Ring 1 (2026-07-13) — a model name from the wrong backend

`[operator testimony]` An agent was configured with `backend: claude-code` and
`model: Claude-opus-4-8`. A session was then created on that agent with the
**codex** backend — which Owlery permits, because the session's backend is its
own field. The harness passed the agent's model through unexamined:

```python
if ctx.model:
    argv += ["-m", ctx.model]
```
*(`server/harness/codex.py`, Owlery — unchanged as of 2026-07-20)*

So `codex exec -m Claude-opus-4-8` was spawned. Codex accepted the flag,
requested a model that does not exist on its service, and — per the operator's
notes — the turn ended after 11–30 seconds with a terminal success record, a
null final message, and **zero error events**. Owlery read that as a turn that
succeeded and had nothing to say.

**The wrong path taken first.** The initial hypothesis was the model's: *it
decided not to answer.* This is the trap. The system was, from every angle it
could see, working — so the only remaining variable seemed to be the model's
judgment. It cost repeated re-sends before the model name was even looked at,
because nothing in the failure pointed at configuration.

### Ring 2 (2026-07-19) — the same face, a different rejection

`[operator testimony]` A second agent showed the identical symptom with a
*correct* model name. The cause this time was a server-side 400 telling the
client it required a newer version of Codex — the local build, `0.142.5`, was
too old for the model being requested. Same 0-token empty turn, same silence.

That is the generalization, and it is worth more than either individual bug:

> **Every server-side rejection Owlery could not render wore the same face.**
> The failure mode is not "a bad model name". It is that a class of upstream
> refusal reached the user as an *absence*, and absences are indistinguishable
> from each other.

### Ring 3 (2026-07-19) — the upgrade that did not take

`[operator testimony]` `npm install -g @openai/codex` was run. It reported
success. The symptom persisted, and `codex --version` still printed `0.142.5`.

Two installs existed. The upgrade landed at `/opt/homebrew/bin/codex`
(`0.144.6`); `PATH` resolved `codex` to `~/.local/bin/codex` first, which was a
symlink to the older build. `which codex` — singular — showed one path and told
you nothing was wrong with it.

This ring has the only surviving physical evidence of the whole episode, and it
is a filesystem timestamp:

```
/opt/homebrew/lib/node_modules/@openai/codex/package.json   2026-07-19 10:18:13
~/.local/bin/codex -> /opt/homebrew/bin/codex               2026-07-19 10:37:04
```

The upgrade at 10:18; the symlink repointed at it 19 minutes later. Those 19
minutes are ring 3.

> `which -a` — not `which` — is the first command of this diagnosis, and it
> should have been the first command of the previous ring too.

### The second-order cause

Three different upstream problems produced one indistinguishable symptom
because **two independent defects in the consumer lined up**:

1. **Nothing checks that a model name belongs to the backend that will run it.**
   `Agent.model` is a free-form `str | None`; `Agent.backend` and
   `Session.backend` are separate enum fields; a session may override the
   agent's backend without the model coming along. No validation joins them, and
   the string is handed to the CLI verbatim.

2. **A terminal success is never asked what it produced.** This is the one that
   turns a diagnosable configuration error into an invisible one — and it is
   still true today. See [§5](#5-discrimination-and-defense), where it is
   reported as found rather than as fixed.

## 3. Evidence

This entry rests on **two evidentiary bases that must not be mixed**, and the
honest report includes a reproduction attempt that *failed*.

### The consumer-side defect — `trace replay`

`repro.py` spawns a fake `codex` that replays a real captured stream over a real
pipe with the captured exit code, and drives it through Owlery's classification
logic, transcribed. It reproduces, deterministically and offline, a turn that is
classified as a success and delivers nothing to the user. That part is proven.

Five captures, taken 2026-07-20, listed in
[`evidence/README.md`](evidence/README.md) with prompts, flags, exit codes and
token cost. The load-bearing one:

| file | terminal record | assistant content | error records | exit |
|---|---|---|---|---|
| [`codex_0.144.6_zero_content_turn.jsonl`](evidence/codex_0.144.6_zero_content_turn.jsonl) | `turn.completed` | `agent_message` with `text: ""` | none | 0 |
| [`codex_0.144.6_control_turn.jsonl`](evidence/codex_0.144.6_control_turn.jsonl) | `turn.completed` | `agent_message` with `text: "OK"` | none | 0 |

Structurally identical. One of them is the failure.

### The attempted reproduction that failed — and why it is in the entry

The incident's exact configuration was re-run today on **both** CLI versions:
the incident's build `0.142.5`, and the current `0.144.6`. Neither is silent
anymore. Both emit an explicit `error` and `turn.failed` carrying

> `{"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The 'Claude-opus-4-8' model is not supported when using Codex with a ChatGPT account."}}`

and both exit **1**. The same `0.142.5` binary handles a normal turn correctly
in the same session, so the rejection is not an artifact of running an old build
today.

Two consequences, both stated plainly rather than smoothed over:

- **The silence was server-side, not a CLI bug.** The old binary is loud today.
  Whatever produced a null message with no error on 2026-07-13 changed at the
  service, not in the client — which also means the ring-3 upgrade, on its own,
  was not what fixed it.
- **This entry cannot and does not claim a live reproduction of the incident.**
  `repro.py` oracle 2 asserts that today's rejection is *loud*, on purpose, so
  the entry cannot quietly regrow a claim its artifacts do not support. An oracle
  that pins the thing you *failed* to reproduce is cheap insurance against your
  own prose drifting.

The `zero_content` capture was obtained by asking the model to emit no message —
a different cause, the same consumer-visible shape. It is a real unedited
capture of the shape, not a reconstruction of the incident, and
[`evidence/README.md`](evidence/README.md) says so at the top.

### The incidents — `operator's diagnostic notes`

The 11–30 second durations, the 0-token turns, the null final message, the
"requires a newer version of Codex" 400, the misdiagnosis sequence and the
dual-install discovery are **all from the operator's notes**. No terminal
transcript, log file or event capture from 2026-07-13 or 2026-07-19 is in hand.
Reported as recollection, not measurement.

What is independently checkable:

- **The 07-19 remediation**, from filesystem mtimes: the 0.144.6 install at
  10:18:13 and the `~/.local/bin/codex` symlink repointed at it at 10:37:04.
  This corroborates the dual-install story and its timing.
- **Both defects in the consumer**, which are not testimony at all — they are
  readable in Owlery's source as it stands on 2026-07-20, and are quoted in
  [§5](#5-discrimination-and-defense).
- **No commit records either incident.** Both fixes were operational (change the
  model field; repoint a symlink), so Owlery's history is silent on them. Its
  log does place active development on both dates.

### Known limits

Single machine, single account, single plan tier, one day of capture. The
captures establish what these two CLI builds do *today*; they establish nothing
about 2026-07-13. The `zero_content` capture shows the shape is reachable, not
that it is common. And the consumer logic in `repro.py` is a transcription of
Owlery's, not an import — it is faithful as of 2026-07-20 and pinned by the
quotes in §5, but it is a copy.

## 4. Minimal case

```bash
cd entries/silent-empty-turn-cross-backend-model-mismatch
python3 repro.py        # exit 0 = every oracle held
```

Offline, no account, no network, no API spend, ~1 second. Standard library only;
imports nothing from Owlery. The fake CLI reads a trace off disk and writes it
out verbatim — it has no idea what any record means, so it cannot be staging a
puppet show for the conclusion.

Five machine-checked oracles:

1. **The silence, reproduced.** The real zero-content capture, driven through
   Owlery's shipped logic: verdict `success`, zero text delivered, zero errors
   surfaced, exit 0 — all four asserted exactly. That conjunction *is* the bug;
   any one of them being non-empty would have made it diagnosable.
2. **Today's rejection is loud — on both CLI versions.** Asserts verdict
   `failed`, exit 1, and the 400 text reaching the user, for `0.142.5` and
   `0.144.6` alike. This oracle exists to keep the entry honest about the
   reproduction it does **not** have.
3. **The CLI's own warning is dropped.** In the rejected stream the very first
   record after `thread.started` is an item saying ``Model metadata for
   `Claude-opus-4-8` not found``. The parser has no branch for that item type,
   so it returns `[]` and the warning is discarded. Asserted both ways: the
   record is present in the stream, and absent from what the user is shown.
4. **The defense.** The guarded policy fails the zero-content turn with an
   explicit message, leaves *both* control turns green and their text intact,
   leaves the already-loud rejection classified exactly as before, and surfaces
   the dropped warning.
5. **The `PROPOSED` pre-spawn attribution check.** Rejects the incident's exact
   configuration, fails in *both* directions (a `gpt-` name on `claude-code`
   too, so it is not one hardcoded string), accepts matching pairs, and passes an
   unrecognised name **through** rather than blocking it.

Regression tests for the defense:

```bash
python3 test_defense.py            # or: python3 -m pytest test_defense.py -q
```

17 tests, ~1.4s. The bug pinned; the fix; no false positive on either control;
the loud path unchanged; the dropped warning in both versions; three shapes the
corpus does not contain (whitespace-only text, an `agent_message` with no `text`
field, a turn with no message item at all — labelled synthetic in the file, and
never cited as evidence about the vendor); the attribution check in both
directions plus its deliberate pass-through; and two tests that assert the
*evidence files themselves* still say what §3 claims, so the prose cannot drift
away from the traces.

**Both guards were mutation-tested.** Disabling guard 1 turns 4 tests red and
`repro.py` red; making it fire unconditionally turns a different test red and
`repro.py` red. The oracles assert in both directions rather than only being
satisfiable.

## 5. Discrimination and defense

### Diagnosing it, in the order that costs least

1. **`which -a <cli>`, never `which`.** Plural. If two paths come back, resolve
   both and print both versions before believing anything else. A silent upgrade
   that landed in the install you are not running will otherwise waste a day —
   and it will do it *after* you have correctly identified and fixed the real
   bug, which is the worst possible time.
2. **Read the first record of the stream, not the last.** In the rejected
   capture the CLI names the exact problem in its second line, before the request
   is even made. The runtime threw it away because it had no branch for that item
   type. Log unmodelled records at debug rather than dropping them.
3. **Check the model against the backend before spawning.** Free-text config
   fields that are handed to a subprocess deserve a check that they belong to
   the subprocess.
4. **Then suspect the model's judgment.** Not before. "It chose not to answer"
   is the hypothesis of last resort, and it was the hypothesis of first resort.

### The defense that closes the class

The check is one line, and it does not need to know anything about model names,
versions, or vendors:

```python
# A terminal SUCCESS that delivered no assistant content is not a success.
if not turn_failed and not delivered_any_assistant_text:
    turn_failed = True
    surface("Turn ended with no assistant output.")
```

That is what makes it worth shipping. It does not enumerate the causes — it
asserts the invariant, so it catches ring 1, ring 2, and whatever ring 4 turns
out to be. `test_defense.py` pins that it fires on an empty turn, does not fire
on a good one, and does not disturb a turn that already failed loudly.

**The trade-off, stated because it is real.** The guard keys on assistant *text*.
A turn that legitimately ends after tool calls with nothing to say would be
flagged. For a chat-shaped agent that is the correct call — the user asked a
question and got no answer, whatever happened in between. For an autonomous
worker that reports only by side effect, the predicate should widen to "produced
no output of any kind" rather than be removed.

### What Owlery actually does — reported as found

Both defects are **still present** as of 2026-07-20. Writing this up as fixed
would have been easy and would have been false.

**Nothing validates the model against the backend.** `Agent.model` is
`str | None`, `Agent.backend` and `Session.backend` are independent enum fields,
and a session may override the agent's backend without the model following. The
harness does no attribution check (`server/harness/codex.py`):

```python
if ctx.model:
    argv += ["-m", ctx.model]
```

**A successful turn is never asked what it produced.** The parser maps
`turn.completed` to a non-error `result` regardless of content, and drops an
empty message before it can be counted (`server/harness/codex.py`):

```python
if kind == "turn.completed":
    return ParseOutput(events=[HarnessEvent(type="result", ...)], end_of_stream=True)
...
if item_type == "agent_message":
    text = item.get("text")
    if completed and text:            # "" is falsy — a zero-content message
        return [HarnessEvent(type="text", content=text, raw=item)]
    return []                          # ...produces no event at all
```

and the disposition never looks (`server/session_manager.py`):

```python
turn_failed = saw_error_event or not saw_result
```

**The near-miss is the instructive part.** The signal the guard needs *already
exists in that function*:

```python
if event.type == "text" and event.content and event.content.strip():
    saw_text = True
```

`saw_text` is computed on every turn — and then consulted only *inside* the
`if turn_failed:` branch, to decide whether a retry may resume rather than
re-run. On the success path the function reaches `if saw_result: return` and
exits without ever reading it. The variable that would have caught this was one
`if` away, on the wrong side of a branch.

**And the third one, on the same path.** `_item_events` returns `[]` for any
item type it does not model — including `item.type == "error"`, which is exactly
the record carrying ``Model metadata for `Claude-opus-4-8` not found``. The CLI
diagnosed the bug in its own output on day one and the runtime deleted it.

None of these is exotic. Each is the ordinary shape of consumer code written
against the happy path: map what you render, ignore what you don't, and treat
"the process exited without complaining" as success.

The lesson generalizes past model names:

> **A channel that cannot express failure will report failure as success.** If
> your only "it worked" signal is *the absence of an error*, then every upstream
> problem your parser does not model becomes indistinguishable from a normal,
> quiet, correct turn. Assert on what a success *produced*, not on what it
> failed to complain about.

**Detection in production**: count turns that end successfully with zero
assistant output. In a healthy system that number is approximately zero and
never trends. It is a single counter, it needs no knowledge of the failure's
cause, and it would have made all three rings visible on the first day.

---

## 中文摘要

**现象**:给 agent 发消息,它「已读不回」。turn 跑 11–30 秒后结束,没有回复,也**没有任何报错**:CLI 退出码 0,事件流以「终态成功」记录收尾,运行时把这一轮标记为完成并放行队列,UI 渲染出一个没有助手消息的完成轮次。整条链路上每一层都报告成功。没有可以 grep 的错误串、没有非零退出、没有栈、没有失败计数——这次故障留下的唯一痕迹是一处**缺席**,而缺席不进日志。崩溃会告诉你去哪儿看,静默的成功什么也不告诉你,而最自然的第一假设「模型自己选择不回答」既合理又完全错误。

**根因(三连环,以下事故叙述均为 `[operator testimony]`,来自操作者当时的诊断记录;两天的终端记录与事件流均未留存)**:同一个症状在六天里被诊断了三次,每次病因都不同——这正是本条目的要点:该症状不是任何单个 bug 的指纹,而是**一整类上游失败经由一条无法表达失败的通道抵达用户**时的指纹。
- **第一环(07-13)**:agent 配置 `backend: claude-code` + `model: Claude-opus-4-8`,而会话以 **codex** 后端创建(Owlery 允许,因为会话的 backend 是独立字段)。harness 原样把模型名透传:`if ctx.model: argv += ["-m", ctx.model]`。codex 接受了这个 flag,去请求一个在它服务上不存在的模型,turn 便以「终态成功 + 空最终消息 + 零错误事件」结束。**先走的弯路**:先怀疑模型自己不想答——因为从系统能看见的每个角度它都在正常工作,唯一剩下的变量似乎只有模型的判断。
- **第二环(07-19)**:另一个 agent 用**正确**的模型名出现同样症状,病因是服务端 400「requires a newer version of Codex」(本机 0.142.5 过旧)。**推论比单个 bug 更值钱:任何 Owlery 渲染不出来的服务端拒绝,都长着同一张脸**——因为它们都以「缺席」抵达用户,而缺席之间彼此无法区分。
- **第三环(07-19)**:`npm install -g` 报成功,症状依旧,`codex --version` 仍是 0.142.5。双安装:升级落在 `/opt/homebrew/bin/codex`(0.144.6),而 PATH 先命中 `~/.local/bin/codex` 这个指向旧版的符号链接。`which codex`(单数)只显示一条路径,并告诉你它没问题。**诊断第一条命令应是 `which -a`,不是 `which`。**

**证据等级(两级,不可混用),以及一次「失败的复现」**:*消费者侧缺陷*是 `trace replay`——`repro.py` 用 fake CLI 通过真实管道回放真实捕获的事件流,确定性地重现「被判为成功却什么都没交付」的一轮。但**事故本身复现失败,且这一点被写进了条目**:用事故当天的构建 `0.142.5` 和当前的 `0.144.6` 重跑事故的原始配置,两者**今天都会明确报错**(`error` + `turn.failed`,退出码 1,消息为 400 `The 'Claude-opus-4-8' model is not supported when using Codex with a ChatGPT account.`);同一个 0.142.5 二进制在同一次会话里跑正常 turn 完全正常,所以这不是「拿旧版今天跑」的假象。由此两条结论照实写出:**(a) 当年的静默来自服务端而非 CLI**,旧二进制今天是响的,因此第三环的升级本身并不是修好它的原因;**(b) 本条目不声称、也无法声称对该事故的 live reproduction**——`repro.py` 的 oracle 2 专门断言「今天的拒绝是响的」,把这条**没能复现**的事实固化成断言,防止日后文案悄悄膨胀。空 turn 的捕获样本则是通过「让模型不要输出任何消息」得到的:**同样的形状,不同的成因**,是真实未经编辑的捕获,不是对事故的重建。事故细节(11–30 秒、0 token、空最终消息、误诊顺序、双安装)全部是口述;唯一幸存的物证是文件系统时间戳:0.144.6 装于 07-19 10:18:13,`~/.local/bin/codex` 符号链接在 10:37:04 被重新指向它——那 19 分钟就是第三环。

**防御**:关键的一行不需要知道任何模型名、版本或供应商——**「终态成功但没有交付任何助手内容」不是成功**,直接判失败并显式报错。它不枚举病因,而是断言不变量,因此第一环、第二环、以及将来的第四环都能接住。代价照实说:该判据以助手**文本**为准,一个合法地「只做工具调用、无话可说」的 turn 会被误报;对聊天形态的 agent 这是正确取舍(用户问了问题却没得到回答),对只靠副作用汇报的自主 worker,应把谓词放宽为「没有产生任何形式的输出」,而不是取消它。

**Owlery 的真实情况——如实报告,两处缺陷截至 2026-07-20 仍然存在**,写成「已修复」很容易,但那是假的:(a) **没有任何地方校验模型名是否属于将要运行它的后端**,`Agent.model` 是自由文本,`Agent.backend` 与 `Session.backend` 是彼此独立的枚举字段,会话可以覆盖后端而模型不跟着走;(b) **成功的 turn 从不被追问它产出了什么**:解析器把 `turn.completed` 无条件映射成非错误的 `result`,而 `if completed and text:` 里空字符串是 falsy,零内容消息**连事件都不会生成**,最终 `turn_failed = saw_error_event or not saw_result` 根本不看内容。最有教益的是那个**擦肩而过**:需要的信号 `saw_text` 就在同一个函数里、每一轮都在计算,却只在 `if turn_failed:` 分支内部被读取(用于决定重试是 resume 还是重跑);成功路径走到 `if saw_result: return` 就退出了,从未读它一眼。那个本可以接住这个 bug 的变量,只差一个 `if`,却待在分支的错误一侧。同一条路径上还有第三处:`_item_events` 对任何它没建模的 item 类型 `return []`——包括 `item.type == "error"`,而那正是承载「Model metadata for \`Claude-opus-4-8\` not found」的记录。**CLI 在第一天就在自己的输出里诊断出了这个 bug,而运行时把它删掉了。** 通用教训:**一条无法表达失败的通道,会把失败报告成成功**;如果你判断「成功」的唯一依据是「没有出现错误」,那么你的解析器没建模的每一个上游问题,都会与一次正常、安静、正确的 turn 无法区分。**要对成功「产出了什么」做断言,而不是对它「没抱怨什么」做断言。** 生产检测:统计「成功结束且零助手输出」的 turn 数——健康系统里这个数约等于零且不会趋势上升,一个计数器,不需要知道任何病因,三个环在第一天就都会可见。
