# Agent Note: ABI v3 逐序列失败隔离 + 后端能力对账（max_batch_seqs / logprobs）

Status: implemented

## Problem

三个耦合的契约缺口：

1. **失败粒度**：executor 里任何序列失败都上抛整批
   `EngineError::BackendError`，健康序列被株连；调度器也无从把失败归因
   到具体 `seq_id`。
2. **能力不对账**：`ExecutorCapabilities` 只有 `{sampling, retry_safe}`，
   调度器批量上限与后端真实可执行规模各记各的账——
   [2026-11-06-tiny-llm-admission-not-clamped](../bug-fix/2026-11-06-tiny-llm-admission-not-clamped.md)
   用构造期 `clamp_config` 把 `max_num_seqs`/`max_batch_size` 夹到
   `backend_max_seqs()` 堵住了 500 过载，但那只是 TinyLlmExecutor 一处
   的特判，通用 executor 仍无对账入口；`logprobs` 请求对不支持的后端
   也是静默。
3. **容量权威错置**：`decode_reserve` 幻影前置限制对两种策略一刀切，
   而策略 1 的真实容量约束是共享 block pool + `max_seq_len`——越界
   请求在错误的位置失败，测试也无法按策略区分归因。

## Decision

- `ExecutorCapabilities` 扩展为
  `{sampling, retry_safe, max_batch_seqs, logprobs}`。引擎构造时经
  `Scheduler::reconcile_backend_batch_cap` 把调度批量上限收敛到后端
  声明的能力；`logprobs=false` 的后端在 submit 阶段以
  `UnsupportedGenerationMode` 显式拒绝 logprobs 请求。
- `ExecutionOutput` 允许为调度集合**真子集**：执行器返回里缺失的
  `seq_id` 由 `fail_sequences` 归因
  `"backend reported per-sequence failure (seq_status)"`；
  `validate_execution_output` 从"必须等长"放宽为子集语义。
- `TinyLlmExecutor::execute` 前置过滤：分配失败、策略 2 容量越界、
  空块表、超 `max_batch_seqs` 的序列置 `viable=false` 排除出本步
  FFI 调用——它们以"缺失输出"形式进入逐序列归因路径，不株连
  viable 序列。
- 容量权威按策略分流：策略 1 越界由调度器共享块池耗尽报
  `Failed to allocate KV block`；策略 2 由 `decode_reserve`
  逐序列预留耗尽经缺失输出归因。`decode_reserve` 只对策略 2 生效。
- `src/tiny_llm_ffi.rs` extern 同步 ABI v3 签名（`seq_status` 出参）；
  `positions` 按 v3 契约记为惰性参数（tiny-llm 侧后端内部跟踪），
  对应 tiny-llm
  [2026-11-09-abi-v3-seq-status-graphs](../../../tiny-llm/.agents/notes/implemented/feature/2026-11-09-abi-v3-seq-status-graphs.md)。

与既有决策的关系：`reconcile_backend_batch_cap` 是能力声明驱动的通用
对账入口，与 admission clamp（构造期配置夹紧）互补共存——前者约束
调度器组批，后者约束准入配置；clamp 决定不受影响。

## Alternatives considered

- **失败序列在 executor 内直接构造 `ExecutionOutput` 错误项。**
  让"缺失即失败"由引擎统一归因更简单：执行器只需保证 viable 序列
  的输出正确，归因逻辑集中在一处，且对"后端静默丢输出"这类未来的
  真实故障天然兼容。
- **把 `tinyllm_allocate_sequence` 等底层错误串透传到终态错误。**
  透传要求 `ExecutionOutput` 携带逐序列错误载体——契约复杂度上移；
  统一的 `per-sequence failure` 归因 + executor 侧 debug 日志已经
  保留定位手段（`TLLM_FFI_DEBUG`），不为字符串断言扩 ABI。
- **`decode_reserve` 对两种策略都保留为前置限制。** 策略 1 下它只是
  幻影上限（真实约束是块池），留着会让策略 1 越界在错误位置失败、
  测试无法鉴别；砍掉后越界语义与容量权威对齐。
- **沿用 `25811e3` 的取消实现一并 rebase。** `git range-diff` 确认它与
  master `59d90c8`（复用 PR #23 设计）配对，保留会产生双实现，丢弃。

## Consequences

Benefits:

- 单序列故障隔离：batch 内健康序列继续，终态错误归因到具体请求。
- 后端能力成为一等契约：新增后端只需如实声明 capabilities，调度侧
  自动收敛；过载回到准入/批量的正确位置（429/逐序列失败）而非 500。
- 越界行为可按策略鉴别：策略 1 报块池耗尽、策略 2 报逐序列失败，
  `tiny_llm_backend_decode_overrun_reports_clear_error` 双策略各自验证。

Costs and limits:

- `ExecutionOutput` 子集语义是引擎—执行器契约变更：返回完整集合的
  mock/第三方 executor 不受影响，但"丢输出"从 bug 变成了受支持语义，
  未来要靠 `fail_sequences` 归因文案区分"真丢"与"主动排除"。
- 逐序列归因文案是通用字符串：底层分配错误的细节只在 executor 日志，
  依赖 debug 开关定位；若运营需要结构化错误载体，需再扩契约。
- 与 tiny-llm 是 ABI 级耦合：FFI 签名 v2/v3 混用即崩，双仓 PR 必须
  同批评审合并。

## Verification

- `cargo test` 默认套件全部通过（含 17 doc-tests）。
- `cargo check --features tiny-llm` + 真实 `libtiny_llm.a` 链接通过。
- `tiny_llm_backend` 4/4 × 策略 1 / 策略 2 真实模型（Qwen2.5-0.5B）：
  `decode_overrun_reports_clear_error` 双策略分流验证；
  `withheld_release_control_distinguishes_strategies` 验证策略 2
  槽位耗尽经逐序列归因、策略 1 不受限。
