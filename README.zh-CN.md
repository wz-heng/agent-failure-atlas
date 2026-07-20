# 失效模式图鉴(Agent Failure Atlas)

一份 AI agent 系统失效模式的图鉴——**只收我自己亲手撞过的坑**,每条都附上支撑
它的证据,以及一个离线、一分钟内跑完的最小复现案例。

每个条目回答五个问题:表面现象是什么、真正的机制是什么(**包括我最初误诊走过
的弯路**)、支撑结论的证据是什么、等级多高、如何复现、生产系统里如何检测与防御。

**这些坑来自哪里。** 来自开发和运行 **Owlery**——一个个人 agent 平台
(FastAPI + React),把 Claude Code 和 Codex CLI 驱动成长时运行的 agent 会话:
定时任务、agent 间委派、消息桥接。Owlery 仓库本身是私有的;每个条目都以脱敏、
自包含的形式把证据带了出来,因此这里的任何内容都不依赖对它的访问权限。

**为什么做这个。** 模型越强,「它到底做对了没有」反而越难肉眼判断。关于这类系统
如何失效的知识,其价值与模型能力正相关——补丁是消耗品,记录在案的失效模式不是。
另外:博客写的是「我的观点」,带证据分级的可复现案例是「可核验的事实」。

> 英文是唯一 canonical 版本。本文是仓库导览;每个条目的完整内容见其英文
> README,文末附中文摘要。

---

## 条目

| # | 失效模式 | 现象 | 证据等级 | 影响 | 修复 |
|---|---|---|---|---|---|
| 1 | [限额判别被真实样本证伪](entries/string-classifier-falsified-by-real-samples/) | Claude 侧配额耗尽与服务端限流均为 HTTP 429,且限流那条的限额词汇比真实限额还多;Codex 侧真实限额流中则完全没有 429 | ![trace replay](https://img.shields.io/badge/evidence-trace%20replay-blue) | 会话无人值守地死上数小时;或因两秒的抖动空挂五小时 | 分后端:Claude 用结构字段(`rateLimitType` + `resetsAt`);Codex 无此字段,改用经正负两类真实 trace 固定的字符串 marker,纪元则从 rollout 按结构读取 |
| 2 | [只杀进程组 leader 会泄漏后代并阻塞后续任务](entries/leader-only-kill-leaks-descendants/) | 测试稳定卡在同一百分比、委派反复失败——因为一个谁也看不见的进程已经占住了它们要用的资源 | ![live reproduction](https://img.shields.io/badge/evidence-live%20reproduction-brightgreen) | 大量时间浪费在误诊上:症状出现在无辜的代码里,而争用状态下做的任何 A/B 实验都只是抛硬币 | 用 `start_new_session=True` 生成;teardown 时对**进程组**发信号并 reap——两半都要,否则只是把孤儿换成僵尸 |
| 3 | [静默的空 turn:一个属于另一个后端的模型名](entries/silent-empty-turn-cross-backend-model-mismatch/) | agent「已读不回」——turn 正常结束、CLI 退出码 0、事件流以成功收尾,里面就是没有回答 | ![trace replay](https://img.shields.io/badge/evidence-trace%20replay-blue) | 六天里三个互不相干的上游故障长着同一张脸,而最自然的第一假设「模型自己选择不回答」既合理又错误 | 对成功**产出了什么**做断言:终态成功却没有任何助手输出即判为错误。外加一道 spawn 前校验(模型名是否属于将要运行它的后端),以及诊断第一条命令用 `which -a` 而非 `which` |

后续条目在各自证据成熟后逐条发布,不攒批。

---

## 证据等级

每个条目都标等级。等级是对「这份材料到底证明了什么」的声明,标注刻意从严。

| 等级 | 含义 |
|---|---|
| ![live reproduction](https://img.shields.io/badge/evidence-live%20reproduction-brightgreen) | 真实依赖的行为可低成本重现。 |
| ![trace replay](https://img.shields.io/badge/evidence-trace%20replay-blue) | 回放脱敏的真实事件流,稳定复现消费者侧如何失效。供应商行为由版本化的 trace 证明,而非重新触发。 |
| ![mechanism simulation](https://img.shields.io/badge/evidence-mechanism%20simulation-yellow) | 最小模型演示同*类*机制。显著标注,**不声称**重现原事故。 |

等级描述的是条目的**可运行产物**。没有产物支撑的叙述——比如操作者对某次事故的回忆——根本不进这个等级体系,而是在正文中就地标注为「口述/testimony」,并单独说明存在哪些独立旁证。一个条目可以同时包含「已分级的产物」与「未分级的叙述」,但必须把两者明显分开。

## 本仓库的自我约束

- **默认案例离线、无账号、无付费、60 秒内跑完。** 没有条目需要供应商账号或消耗
  API 额度。
- **每条都有机器判定的 oracle**——一个会通过或失败的断言,不是打印一堆输出让读者
  自己看。
- **没有条目 import Owlery。** 案例是对其中已上线逻辑的自包含重新实现,因而能
  独立运行、独立阅读。
- **不按结论手编材料。** 凡是由 fake 进程回放事件流的地方,回放的都是*捕获到的
  真实*事件流,绝不上演一出印证论点的木偶戏。
- **不收转述案例。** 不是我亲手撞过的,不进这里。
- **模拟绝不写成实证**,相关性绝不写成因果。
- **可复现性绑定版本,不做永久承诺。** 供应商会改行为;条目记录它成立的版本,并
  用 trace 保存历史行为。
- **不出现任何私有 transcript、路径、session ID 或凭据。** 每个条目都写明脱敏了
  什么。

## 目录结构

```
entries/<slug>/
  README.md        五段式条目正文
  repro.py         带 oracle 的最小案例——离线,<60s
  test_defense.py  防御的回归测试
  evidence/        脱敏后的捕获样本 + 来源与脱敏说明
docs/              设计短文
```
