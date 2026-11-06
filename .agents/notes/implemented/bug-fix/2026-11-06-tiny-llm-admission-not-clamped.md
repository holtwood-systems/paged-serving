# Agent Note: tiny-llm 后端过载时 500 而非 429——调度器准入未对齐后端容量

Status: implemented

## Problem

2026-11-06 故障注入 E3（64 并发洪峰，`PAGED_SERVING_TINY_LLM_MAX_SEQS=8`）
实测：全部请求返回 **500 internal_error**，响应体
`Kernel 启动失败: Batch size 16 exceeds max 8`，而不是准入控制应有的
200/429。根因链：

1. `PAGED_SERVING_TINY_LLM_MAX_SEQS` 只影响 `TinyLlmExecutor` 自身的
   batch 上限（构造时 `min(config.max_batch_size, max_concurrent_seqs)`）；
2. 调度器准入用 `config.max_num_seqs`（默认 256）与
   `config.max_batch_size`（默认 32），两者与后端容量**没有任何联动**；
3. 过载时调度器把 16 条序列组进一个 batch，executor 前置检查按
   "Batch size 16 exceeds max 8" 报 `EngineError::BackendError`，
   HTTP 映射为 500 internal_error——把可预期的过载变成了内部错误，
   且 `errors_total` 被污染。

`max_num_seqs` 的准入语义本应触发
`EngineError::MaxConcurrentSequencesReached` → 429（server.rs 注释明确
"过载（内存压力 / 并发上限）→ 429"），实际却从未生效于后端容量约束。

## Decision

`create_engine` 的 TinyLlm 分支在构造 `Scheduler` 前把配置夹到后端容量：

```rust
let backend_max = TinyLlmExecutor::backend_max_seqs(); // MAX_SEQS_ENV
config.max_num_seqs = config.max_num_seqs.min(backend_max);
config.max_batch_size = config.max_batch_size.min(backend_max);
```

效果：超出后端容量的请求在准入阶段收到
`MaxConcurrentSequencesReached` → 429 + Retry-After；batch 永远
不超过后端可执行大小；executor 前置检查保留为防御性断言。

## Alternatives considered

- 在 `execute` 报错处把 BackendError 映射成 429：掩盖根因——过载判定
  应在准入发生，而不是等组好 batch 再让后端拒绝；且 batch 里其他
  序列的公平性无法恢复。
- 只改文档提示"把 max_num_seqs 设小"：默认配置仍是陷阱，500 依旧。
- 在调度器 schedule() 处截断 batch：同一批里超出的序列被饿死，
  不如准入拒绝语义清晰。

## Risks

- 夹紧后单机并发上限从 256 降到后端容量（默认 4 / 本环境 8）——
  这是后端真实容量，行为更诚实；但依赖 `max_num_seqs` 做高并发的
  CPU 参考后端不受影响（仅 TinyLlm 分支夹紧）。
- 环境变量在 `create_engine` 时读取一次，运行期改 env 不生效
  （与 executor 现有行为一致）。

## Consequences

- `PAGED_SERVING_TINY_LLM_MAX_SEQS=8` 下 64 并发洪峰只出现 200/429，
  无 500，`paged_requests_total` 增量精确为 64。
- 既有单测与真实后端测试不回归（`cargo test --features tiny-llm`）。
- 不设环境变量时默认容量 4 同样生效。

## Verification

2026-11-06 实测通过：修复后 E3 同场景（64 并发、后端容量 8）
返回 **200×8 + 429×56，零 500**，`requests_total` 增量 64，
资源回零（证据 `tests/evidence/2026-11-06-fault-injection/`）。
单测 `clamp_config_caps_admission_and_batch_to_backend` /
`clamp_config_keeps_config_below_backend` 通过。
