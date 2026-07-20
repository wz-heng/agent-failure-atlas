# Killing only the process-group leader leaks descendants that block the next run

> **Evidence level: `live reproduction`** — for the *mechanism*.
> [`repro.py`](repro.py) runs real processes on your machine, kills a leader,
> observes a real orphaned grandchild holding a real port, and shows the next
> run deterministically failing to acquire it. It is **not** a replay of the
> incident described in §1; that account is operator testimony, labelled as
> such rather than graded. Read [§3](#3-evidence) before quoting either.

| | |
|---|---|
| **Hit in** | Owlery — a personal agent platform driving the Claude Code and Codex CLIs (private repository) |
| **Mechanism repro** | POSIX (macOS + Linux). Verified on macOS 26.3 / Darwin 25.3.0, CPython 3.9.6 and 3.12.13 |
| **Incident** | 2026-07-17, single developer machine |
| **Impact** | Test runs hang at a stable percentage; delegations fail repeatedly; hours lost to misdiagnosis because the symptom appears in code that is not at fault |
| **Repro** | `python3 repro.py` — offline, no account, ~4s |

---

## 1. Symptom

Two symptoms on one machine, looking unrelated:

- `pytest` **hangs at a stable percentage** — the same percentage every run.
  Running the file that appears to hang, on its own, passes in seconds.
- Agent **delegations fail repeatedly**, with errors that point at the network.

Neither symptom names its cause. Both are what *other* work looks like when
something invisible already owns a resource it needs.

The **leak fingerprint**, worth memorising because it is specific:

- The hang sits at a **stable progress percentage**, not a random one.
- That point falls on a **module boundary** — an event-loop teardown between
  test files, not inside a test body.
- The implicated file **passes in seconds when run alone**.
- `faulthandler` produces **no dump at all**, or one pointing at
  `selectors.select` — i.e. a thread waiting on I/O that will never arrive,
  not a deadlock in your code.

That combination says *contention with something outside this process*, not
*bug in the code under test*.

## 2. Root cause — including the wrong paths taken first

### The mechanism

A supervisor spawns a job. The job spawns its own children — a test runner, an
MCP server, a database handle, a subagent. When the supervisor tears the job
down, it signals **the process it has a handle on**: the direct child.

That kills the leader and nothing else. The grandchildren are not signalled,
are reparented to whatever adopts orphans on that system — usually PID 1, but a
subreaper or PID namespace may take them instead — and keep running, still
holding whatever they held. They are now owned by nobody the supervisor knows
about, referenced by nothing, and invisible to every tool it has.

The next run then contends with a process that no longer appears in any process
tree anyone is looking at. `repro.py` demonstrates exactly this, live.

### The wrong paths

> **`[operator testimony]` — everything in this subsection and the next comes
> from the operator's debrief, relayed to an author who was not present. No log
> file or `ps` capture survives. See [§3](#3-evidence).**

Two misdiagnoses came first, and both are instructive:

1. **"It's a code bug."** The hang reproduced at the same place every time,
   which reads like determinism in the code. It was determinism in the
   *contention*: the same resource was grabbed at the same point of every run.
2. **"It's the proxy."** An A/B experiment on 2026-07-17 appeared to confirm
   this — unset the proxy variables, and the suite went green. The conclusion
   was later overturned by deterministic probes.

The second one is the more valuable lesson:

> **While a machine is under resource contention, every A/B experiment is a
> coin flip wearing a lab coat.** A run that goes green after you change
> something tells you nothing if an invisible third party decides, run by run,
> whether you get the resource. Establish that the machine is quiet *first* —
> then experiment.

`[operator testimony]` The cause the operator settled on was contention between
two leaked processes on the same machine:

- a process left over from a **previous agent session** that was still running
  `pytest` — and whose own command line began with `pkill -9 pytest`, so it
  would kill *other people's* test runs as a side effect;
- a `pytest` started from an **expired `/private/tmp` git worktree**, wedged in
  `pytest_asyncio` `_scoped_runner` teardown for roughly two hours, holding
  loopback and database resources.

After both were cleared, deterministic probes ran 6/6 green in subsecond time.

### Orphans are not zombies

This distinction was corrected during review of this entry, and getting it
wrong sends you hunting the wrong thing entirely:

| | orphan | zombie |
|---|---|---|
| `ps` state | `S` / `R` — **alive** | `Z` — **exited** |
| parent | reparented to an adopter — often PID 1 (`init`/`launchd`), possibly a subreaper | still its original parent, until reaped |
| holds ports, locks, memory? | **yes** | **no** — the kernel released everything at exit *(measured: oracle 3 binds a port in the zombie, then rebinds it while the zombie is still in `Z`)* |
| can it wedge your next run? | **yes** | **no** |
| how it's fixed | signal the process **group** | the parent calls `wait()` |

A zombie is an accounting artifact: a slot in the process table kept so its
parent can read the exit status. It holds nothing. **A zombie can never be why
your port is busy.**

That last sentence is the kind of received wisdom this atlas is supposed to
*measure* rather than repeat, so oracle 3 measures it: the zombie process binds
a real loopback port before exiting, and the oracle asserts that the very same
port rebinds immediately while the process is still sitting in `Z`. An earlier
revision simply printed "holds anything: False" as a hardcoded string — true,
but an assertion about the author's beliefs rather than about the machine.

## 3. Evidence

This entry rests on **two different evidentiary bases** — one graded artifact
and one ungraded testimony. Mixing them up would be exactly the overreach this
atlas is supposed to avoid.

**The mechanism — `live reproduction`.** [`repro.py`](repro.py) is a genuine
live reproduction: real `fork`/`exec`, real process groups, real signals, a real
bound port, real `ps` output. It reproduces on demand, offline, in about four
seconds. Nothing about it is simulated or replayed. If it stops reproducing, it
fails loudly.

Verified on **macOS 26.3 (Darwin 25.3.0)**, CPython 3.9.6 and 3.12.13. It is
written to POSIX semantics and should hold on Linux, but **has not been run
there** — that is a reasoned expectation, not a measurement. It asserts
reparenting as "no longer the original parent" rather than "`ppid == 1`",
precisely because a Linux subreaper or PID namespace may adopt orphans instead
of `init`.

**The 2026-07-17 incident — `operator's incident notes`, plus circumstantial
corroboration.** The account in §1 and §2 comes from the operator's
recollection and debrief, relayed to the author of this entry, who was not
present. Specifically:

- Process IDs (`57304`, `66518`), the two-hour wedge duration, the
  `_scoped_runner` teardown location, the `pkill -9 pytest` command prefix, the
  "6/6 subsecond" probe result and the proxy A/B episode are **all from those
  notes**. No log file, `ps` capture, or terminal transcript from the incident
  is in hand. They are reported here as recollection, not measurement.
- What *is* independently checkable in the source repository: the incident is
  said to have happened while the `messenger-form` work was being finished, and
  Owlery's history does place that work on that date — `aadaaf2`
  ("close the round-3 review") and the merge `0d925e0`, both 2026-07-17. That
  corroborates the *timing and context*, nothing more. **No commit records the
  incident itself**, because the fix was operational (kill the leaked
  processes), not a code change.

So: the mechanism is proven, the incident is testimony. Do not cite the incident
numbers as measurements.

**Relationship between the two.** `repro.py` reproduces the mechanism that
*produces* this class of leak — a leader-only kill. The 2026-07-17 leaks were
not created by this script; they came from agent-session and dev-loop cleanup
paths. Same mechanism, different origin. **This is a demonstration of the
mechanism, not a reconstruction of the incident.**

**Known limits.** POSIX only — the case needs process groups and `ps`, and exits
`3` elsewhere rather than pretending to pass. Empirically verified on macOS
only; Linux is expected to hold but untested. It demonstrates one resource (a
TCP port) standing in for a class (ports, file locks, database handles); a port
was chosen because it is exclusive, observable, and behaves the same on macOS
and Linux. `ps` state letters are read as prefixes (`Z…`, `S…`) because the
suffixes differ between platforms.

## 4. Minimal case

```bash
cd entries/leader-only-kill-leaks-descendants
python3 repro.py        # 0 = every oracle held · 1 = a failure · 3 = unsupported platform
```

Offline, stdlib only, ~3.5 seconds, imports nothing from Owlery. It re-executes
itself in three roles (`leader`, `holder`, `exiter`) so the whole case stays in
one readable file. On a non-POSIX platform it exits **3, not 0** — a skipped
platform must not read as a green run to whatever is calling it.

Three machine-checked oracles:

1. **Leader-only kill leaks, and the leak blocks.** Spawn leader → grandchild;
   the grandchild binds a loopback port. Kill *only* the leader. Assert the
   grandchild is in a live (non-`Z`) state, that its parent is no longer the
   leader, and that it still holds the port — then that the next run, given a
   bounded 2-second window, **fails to acquire it**. The timeout is the verdict;
   you never wait on a real hang.
2. **Process-group kill releases.** Same setup, but `killpg` the group and reap.
   Assert the port is free and the grandchild is no longer alive.
3. **An orphan is not a zombie — measured.** A live orphan and a genuine
   unreaped zombie side by side, *both having bound a real port*. Assert the
   orphan is live, reparented, and still holding its port; and that the zombie
   is in `Z`, still our child, and that **its port rebinds immediately**.

**On this script's own hygiene.** Every process group it starts is registered
the moment it exists — together with its leader's `Popen` — and a `finally`
sweep kills each group and reaps each leader, on success, on oracle failure, on
an unexpected exception, on Ctrl-C, and on `SIGTERM`/`SIGHUP` (trapped, because
the default disposition dies without running `finally`).

It took two review rounds to get that right, and both failures were instances of
the bug this entry is about:

- The first revision's error paths **returned early past their own cleanup**, so
  a crashed run would have handed the reader a sleeping orphan holding a port.
- The second revision's "reap" was `waitpid(-1, WNOHANG)` in a loop that stopped
  the moment it returned `0` — which is precisely what it returns when a child
  exists but has not been collected yet, the normal state right after `SIGKILL`.
  Every leader was left in `Z`. **30 out of 30** start→sweep cycles in one
  process left an unreaped leader. It looked clean from outside only because
  those zombies were re-parented and reaped when the whole process exited —
  the "process exit will clean it up" habit this entry argues against, masking
  the leak. Reaping is now per-leader `Popen.wait()`, which waits on its own pid
  and cannot swallow another child's status, and
  `test_the_sweep_reaps_every_leader_over_many_cycles` runs those 30 cycles
  every build.

Neither is a flattering story. Both are better in the entry than in the reader's
process table.

Regression tests for the defense:

```bash
python3 test_defense.py            # or: python3 -m pytest test_defense.py -q
```

10 tests, ~5s, each wrapped in the same sweep: the bug pinned, the fix,
run-teardown-run, teardown idempotency on an already-dead job, no zombie left by
the supervisor itself, a zombie measurably releasing its port at exit, orphan
and zombie contrasted on the only axis that matters, the repro's own sweep
reclaiming an abandoned group *and reaping its leader*, 30 start→sweep cycles in
one process asserting no leader survives as a process-table entry, and a guard
that the blocking demo stays bounded.

## 5. Discrimination and defense

**The fix has two halves, and skipping either one still leaks.**

```python
# 1. Spawn the job into its own session, so it leads a process GROUP
#    and every descendant inherits that group id.
proc = subprocess.Popen(argv, start_new_session=True)

# 2. On teardown, signal the GROUP — then REAP.
os.killpg(os.getpgid(proc.pid), signal.SIGTERM)   # ... escalate to SIGKILL
proc.wait()                                        # reap, or you trade an
                                                   # orphan for a zombie
```

Signalling without reaping trades a leaked orphan for an accumulating zombie —
better, but still a leak. `test_defense.py` asserts both halves separately.

**What Owlery actually does.** Reported as found, including the parts that are
weaker than the headline. It has the two structural pieces, and says why:

> Own process group (session leader) so the whole tree the CLI spawns — MCP
> servers, nested subagents — is reapable as a unit via `killpg`, instead of
> orphaning on `stop()`/`interrupt()`.
> — `server/harness/run.py`

`prepare_spawn` sets `start_new_session=True`, so every CLI turn leads its own
group. `_terminate_process_group` resolves the pgid and `killpg`s it. The design
is written down in `docs/plans/turn-safety.md` §2. Three limits, stated because
they are where this class of bug actually lives:

- **The group sweep is on the forced-termination path only.** Teardown closes
  stdin and waits ~2s; the `SIGTERM`-group → `SIGKILL`-group escalation runs
  *only if the leader has not exited by then*. When the leader exits cleanly —
  the common case — no group signal is ever sent. Descendants that outlive a
  cleanly-exiting leader are not swept.
- **There is no group-empty assertion.** Nothing verifies after teardown that
  the group is actually empty, so a surviving descendant is silent. This entry's
  `test_defense.py` asserts exactly that, and it is the cheapest missing check.
- **The direct-child fallback is narrower than it reads.** It fires only when
  `os.getpgid()` itself fails. If `killpg` raises `ProcessLookupError` or
  `PermissionError`, `_terminate_process_group` returns `False` without falling
  back to signalling the child.

None of this caused the 2026-07-17 incident — see below — but "the subsystem we
thought about is 90% right" is the normal state of affairs, and the missing 10%
is where the next one comes from.

**And what that did not save it from.** The 2026-07-17 leaks were not
CLI-turn processes. They were a `pytest` from a previous agent session and a
`pytest` from a stale worktree — processes born outside the harness seam that
implements this discipline. Getting process-group hygiene right in the subsystem
you thought about does not protect the machine; every path that spawns a
long-lived process needs the same treatment, including the ones that feel like
throwaway developer tooling.

**Detecting it in production, in rough order of cost:**

1. **Before blaming code, prove the machine is quiet.** `pgrep -fl pytest`,
   `lsof -i :PORT`, or a deterministic probe that must pass in subsecond time.
   Do this *before* any A/B experiment, not after two of them disagree.
2. **Read the fingerprint.** Stable percentage + module boundary + passes alone
   + no `faulthandler` dump ⇒ contention, not a code bug. That combination is
   specific enough to act on.
3. **Check `ppid`, not just liveness.** A process whose parent is `1` and which
   nobody is supervising is a leak by definition. `ps -o pid,ppid,stat,command`
   is the whole diagnostic.
4. **Distinguish `Z` from `S`/`R` before you start.** If everything you find is
   `Z`, the leak is elsewhere — zombies hold nothing.
5. **Make teardown assert.** The cheapest permanent fix: after teardown, verify
   the resource is actually reusable. A supervisor that leaks looks identical
   to one that doesn't until the next run — so test the next run.

The lesson generalizes past process groups:

> **A cleanup path that only addresses the handle you happen to hold is not a
> cleanup path.** Ownership in an operating system is a tree; releasing a node
> does not release its subtree. The same shape appears in temp directories,
> file locks, container namespaces, and cloud resources: the thing you forgot to
> enumerate is the thing that outlives you.

---

## 中文摘要

**现象**:同一台机器上两个看似无关的症状——`pytest` **稳定卡在同一个进度百分比**(而且单独跑那个文件秒过),以及委派任务反复报失败、错误信息还指向网络。两者都不指明病因:它们只是「有个看不见的东西已经占住了资源」时,其它工作呈现出的样子。可记忆的**泄漏指纹**是:挂点百分比稳定且落在模块交界(事件循环 teardown,不在测试体内)、单跑秒过、`faulthandler` 无 dump 或指向 `selectors.select`。这组合足以判定为「与进程外的东西争用」,而非被测代码的 bug。

**根因(含误诊路径)**〔以下误诊经过与事故细节均为 **`[operator testimony]`**:来自操作者复盘口述,本条目作者当时不在场,手上没有日志或终端记录〕:监管进程只对**自己持有句柄的那个直接子进程**发信号。leader 被杀,孙进程收不到任何信号,被重新挂到 `init`/`launchd` 名下继续跑,并继续持有它占的资源——此时它无人拥有、无处引用,对监管方的所有工具都不可见。误诊走了两条弯路:先怀疑代码 bug(挂点稳定看着像代码里的确定性,其实是*争用*的确定性);再怀疑 proxy,当天甚至做出「unset proxy → 全绿」的 A/B 结论,后被确定性探针推翻。第二条的教训更值钱:**机器处于资源争用状态时,任何 A/B 实验都只是穿了白大褂的抛硬币**——先证明机器是干净的,再做实验。确诊的争用源是两个泄漏进程:前任 agent session 遗留的 `pytest`(其命令前缀 `pkill -9 pytest` 还会误杀别人的测试),以及一个从过期 `/private/tmp` git worktree 起跑、卡在 `pytest_asyncio` `_scoped_runner` teardown 约两小时、占住 loopback 与数据库资源的 `pytest`。清理后确定性探针 6/6 亚秒全过。

**孤儿不是僵尸**(这条在复核中被纠正过,搞混会把人引向完全错误的方向):孤儿是 `S`/`R` 状态、**活着**、已被重新挂到原父进程之外(通常是 `init`/`launchd`,但 Linux 的 subreaper 或 PID namespace 也可能接管,故断言写的是「父进程已不是原来那个」而非硬编码 `ppid == 1`)、**仍持有端口与锁**,只能靠对**进程组**发信号解决;僵尸是 `Z` 状态、**已退出**、仍挂在原父进程下、**什么都不持有**(内核在它退出时已释放一切),靠父进程 `wait()` 回收。**僵尸永远不可能是你端口被占的原因。**——这句是本图鉴要求「实测而非复述」的典型:oracle 3 让僵尸进程在退出前真的 bind 一个端口,再断言它仍处于 `Z` 状态时该端口能立即被重新 bind。早期版本只是硬编码打印一句 "holds anything: False",那断言的是作者的信念,不是机器的行为。

**证据等级(两级,不可混用)**:*机制*是 `live reproduction`——`repro.py` 在你的机器上真跑进程、真发信号、真占端口、真读 `ps`,约 4 秒离线复现。*2026-07-17 那次事故本身*则是 `operator's incident notes`:进程号、两小时时长、`_scoped_runner` 卡点、6/6 探针结果等全部来自操作者复盘口述,手上没有日志或终端记录,本条目作者当时不在场。可独立考证的旁证只有时间与背景:Owlery 历史确实把 messenger-form 收尾放在 2026-07-17(`aadaaf2`、`0d925e0`),但**没有任何 commit 记录这次事故**,因为修复是运维动作而非代码改动。此外须明说:repro 演示的是*产生*这类泄漏的机制,那次事故的泄漏进程来自 agent session 与开发循环的清理路径,并非本脚本产生——**机制同类,不是同一事件的重放**。

**防御**:修复有两半,少任何一半仍然泄漏——(1) 用 `start_new_session=True` 把作业生成到自己的进程组;(2) teardown 时对**进程组**发信号(`SIGTERM` → `SIGKILL`)**并 reap**;只发信号不 reap,等于把泄漏的孤儿换成堆积的僵尸。**Owlery 的真实情况**如实报告,包括不如标题好看的部分:结构上的两件事它都有(`prepare_spawn` 的 `start_new_session=True` + `_terminate_process_group` 的 killpg,设计记在 `docs/plans/turn-safety.md` §2),但有三处限制——(a) 组清扫**只在强制终止路径上**:teardown 先关 stdin 等约 2 秒,`SIGTERM`/`SIGKILL` 组升级仅在 leader 届时仍未退出时才执行,leader 正常退出(常见情形)则从不发送任何组信号;(b) **没有 group-empty 断言**,teardown 后无人验证组是否真的空了,残存后代是静默的;(c) **直接子进程 fallback 比字面窄**:它只在 `os.getpgid()` 本身失败时触发,若 `killpg` 抛 `ProcessLookupError`/`PermissionError`,函数直接返回 `False` 而不回退到给子进程发信号。这些都不是 07-17 事故的成因——那两个泄漏进程根本**不是** CLI turn 进程,而是诞生于该接缝之外的 `pytest`——但「我们想到的那个子系统做对了九成」才是常态,而缺的那一成正是下一次事故的来源。通用教训:**只处理你手上恰好握着的那个句柄的清理路径,不算清理路径**;操作系统里的所有权是一棵树,释放一个节点不会释放它的子树。
