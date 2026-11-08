# Agent Note: stop 命中与 max_tokens 同一步时仍截断

Status: implemented

## Problem

`InferenceEngine::step_events` 先调用 `scheduler.update_sequences`，再调用
`apply_stop_sequences`：

```rust
self.scheduler.update_sequences(&execution_output, self.eos_token_id);
...
self.apply_stop_sequences(&generated);
```

如果本步生成的 token 既是 stop 序列的最后一个字符，又刚好到达
`max_tokens`，`update_sequences` 已经调用 `complete_sequence` 把请求移入
`completed_requests`。随后 `Scheduler::complete_by_stop_sequence` 只在
`prefill_sequences` / `decode_sequences` 中查找，返回 `false`；引擎却仍无条件
删除 decoder。结果：

- 非流式：终态里的 `output_tokens` 未截断，stop 文本出现在响应中，
  `finish_reason` 因 `stop_sequence_hit == false` 变成 `Length`；
- 流式：decoder 被提前删除，本步 chunk 丢失，拼接文本与非流式不一致。

本地 HTTP 复现（CPU 后端，`max_tokens=1, stop=","`）：
非流式返回 `text="," finish_reason="length"`；同参数流式返回空文本。
现有 stop 测试都用 `max_tokens=10` 且只生成到 stop，未覆盖边界。

## Decision

1. `Scheduler::complete_by_stop_sequence` 在活动序列中找不到时，再查
   `completed_requests`；命中则就地截断 `output_tokens` / `logprobs` 并置
   `stop_sequence_hit = true`，返回 `true`（KV 已释放，不重复完成）。
2. 引擎仅在 `complete_by_stop_sequence` 返回 `true` 时删除 decoder；
   未找到请求时保持现有生命周期，交给 `collect_completed_requests` 统一回收。
3. 新增 `test_stop_sequence_hit_on_max_tokens_boundary_truncates`：
   `max_tokens=2`、输出 `A, B`、`stop="AB"`，断言输出为空、
   `finish_reason=Stop`。

## Alternatives considered

- **把 stop 检测移到 `update_sequences` 之前。** 此时新 token 还没写入
  `output_tokens`，检测不到本步形成的 stop；要重构执行输出与调度器的边界，
  改动面远大于本修复。否决。
- **让 `complete_by_stop_sequence` 重新入队再完成。** 请求的 KV 已被
  `complete_sequence` 释放，重新入队会破坏资源不变量。否决。
- **只在引擎层改 completed request，不改 scheduler。** 引擎持有
  `completed_requests` 的所有权路径（通过 scheduler），绕过 scheduler 会
  复制终态修改逻辑并跳过停止原因的单一来源。否决。

## Consequences

- stop 与 max_tokens 重叠时，非流式与流式都返回截断后的文本和
  `finish_reason=stop`。
- `complete_by_stop_sequence` 的返回值成为 decoder 生命周期的判据；
  未找到请求时不再静默丢弃 decoder。
- 未改变 `max_tokens` 的计数语义：到达上限仍会完成请求，只是输出被截断。

## Verification

- `cargo test --lib test_stop_sequence_hit_on_max_tokens_boundary_truncates`：
  通过（1 passed）。
- `cargo test --quiet`：300 passed / 0 failed / 1 ignored（比改动前多 1 个用例）。
- `cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`：通过。
- 未运行真实 GPU 后端与 HTTP 端到端；本地复现用的是 CPU 参考执行器。
