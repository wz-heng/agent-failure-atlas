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
| 1 | [字符串判别被真实样本证伪:限额检测](entries/string-classifier-falsified-by-real-samples/) | 配额耗尽与服务端限流以相同的 HTTP 429 和高度重叠的文案抵达,但正确处置完全相反 | ![live reproduction](https://img.shields.io/badge/evidence-live%20reproduction-brightgreen) | 会话无人值守地死上数小时;或因两秒的抖动空挂五小时 | 判别键改为「CLI 是否指名了被耗尽的窗口」(`rateLimitType` + `resetsAt`),绝不依赖渲染文案 |

后续条目在各自证据成熟后逐条发布,不攒批。

---

## 证据等级

每个条目都标等级。等级是对「这份材料到底证明了什么」的声明,标注刻意从严。

| 等级 | 含义 |
|---|---|
| ![live reproduction](https://img.shields.io/badge/evidence-live%20reproduction-brightgreen) | 真实依赖的行为可低成本重现。 |
| ![trace replay](https://img.shields.io/badge/evidence-trace%20replay-blue) | 回放脱敏的真实事件流,稳定复现消费者侧如何失效。供应商行为由版本化的 trace 证明,而非重新触发。 |
| ![mechanism simulation](https://img.shields.io/badge/evidence-mechanism%20simulation-yellow) | 最小模型演示同*类*机制。显著标注,**不声称**重现原事故。 |

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
