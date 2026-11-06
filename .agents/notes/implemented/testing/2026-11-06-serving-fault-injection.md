# Agent Note: serving 故障注入实验（客户端侧故障 + 指标断言）

Status: implemented

## Problem

仓内已有取消、背压、类型化指标的单元/集成覆盖（PR #24 批次），
但缺**对真实运行服务施加客户端侧故障**的证据：mid-SSE 断连、
慢消费者溢出、过载洪峰、非流式中断。这些正是 serving 岗位面试
会追问的"故障下行为如何"一类问题，也是 audit 文档登记的
"真实网络故障注入"缺口。现有 tests/ 只有进程内 harness 与
loadgen，均不产生网络层故障。

## Decision

新增 `tests/fault_injection/inject.py`（Python 3 标准库，无新依赖），
对 --base-url 指向的真实服务跑四组实验，用 /metrics 计数器与
gauge 做断言，输出 JSON 证据包到 `tests/evidence/<date>-fault-injection/`：

- **E1 mid_sse_rst**：流式请求读到 2 个 SSE 事件后 SO_LINGER RST
  断连；断言 `paged_engine_cancelled_requests` +1、
  `paged_inflight_requests`/`paged_engine_active_sequences`/
  `paged_engine_kv_utilization` 回零、`/healthz` 200、
  后续普通请求成功。
- **E2 slow_consumer_overflow**：流式 max_tokens=512 只读 1 个事件
  后停顿 25s（event_channel_capacity 默认 64 → 溢出）；断言服务
  存活、并发的探针请求不受影响、inflight 回零、进程 RSS 有界。
- **E3 flood_admission**：64 并发非流式短请求；断言状态码只有
  200/429（无 5xx、无挂起）、`paged_requests_total` 增量恰为 64、
  inflight 回零。
- **E4 nonstream_abort**：非流式请求发出后 50ms 内断连；断言
  cancelled +1 且资源回零。
- **E5 poisson_mixed**：泊松开环流量（loadgen）与 mid-stream RST
  并发注入；断言正常流量零失败、断连不计入 errors_total。

每个实验用"快照→扰动→settle 轮询（inflight/active 回零，超时即
失败）→快照"的模板；失败以非零退出码和 JSON 内 assertion 明细
呈现，不吞错。

## Alternatives considered

- 用 Rust 写进 tests/：能复用 harness，但网络层 RST/慢读需要
  更底层的控制，且证据脚本与 loadgen 职责混淆；Python 标准库
  足够且与 benchmarks/serving/plots.py 的脚本传统一致。
- 用 toxiproxy/netem：引入外部依赖与网络命名空间权限要求，
  单机直连场景收益不匹配。
- 只做单实验：四组分别覆盖取消/背压/准入/中断四条独立路径，
  合并会失去逐路径的可审计性。

## Risks

- 断言依赖指标语义（如 cancelled 的计数时机），上游改动可能
  使阈值漂移；脚本按"settle 后快照"取值并对计数采用 >= 断言。
- E2 的溢出行为受 event_channel_capacity 配置影响，证据 README
  必须记录服务实际配置。
- 时间敏感实验（E4 50ms 断连）在极慢机器上可能退化为
  "请求已完成"，脚本需把该情形记为 inconclusive 而非通过。

## Consequences

- 四组实验在真实 tiny-llm 后端服务上全部跑通且断言通过
  （或对不满足的项输出 inconclusive/fail 明细）。
- 证据包含逐实验 before/after 指标快照与断言结果，README 记录
  硬件、双仓 commit、服务配置与启动命令。
- 不传 --base-url 时脚本只打印用法，无副作用。

## Verification

2026-11-06 对真实后端（RTX 4090、tiny-llm Q4_K_M、MAX_SEQS=8）实测：
**4/4 实验 pass、全部断言绿**（证据
`tests/evidence/2026-11-06-fault-injection/`）。首跑 E3 即抓出准入
契约缺陷（64 并发全 500），修复后 200×8+429×56——见同批
`implemented/bug-fix/2026-11-06-tiny-llm-admission-not-clamped.md`。
E2 停顿期 RSS 增长 26.8 MiB、并发探针正常；E4 生成中取消 +2。
落地与提议的差异：E4 改为两个断连时点（准入前/生成中），准入前
无终态计数记为允许（未进引擎，不泄漏）。
