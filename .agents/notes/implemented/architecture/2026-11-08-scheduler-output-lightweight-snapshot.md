# Agent Note: 调度输出改为轻量快照，去掉每步整序列克隆

Status: implemented

## 问题

`Scheduler::schedule` 在每个 decode 步对每个活跃序列执行
`sequence.clone()` 放进 `SchedulerOutput`。`Sequence` 内嵌 `Request`，
而 `Request` 持有 `input_tokens`、`output_tokens`、`logprobs` 等 Vec：

- 每个 decode 步都深拷贝 prompt + 全部已生成 token（启用 logprobs 时
  还包括每 token 的 top-k 向量）；
- 单请求整段生成为 O(T²) 字节拷贝与每步多次 Vec 分配；
- `build_execution_batch` 随后又为每序列分配一次 block-table Vec。

实际使用方只需要 `seq_id`、prefill 的 prompt、decode 的最后一个 token 与
位置、`context_len`、块表；其余字段在调度输出里从未被读取。

## 决定

把 `SchedulerOutput` 的元素类型从 `Sequence` 换成两个轻量结构：

- `ScheduledPrefill`：`seq_id`、`request_id`、完整 `input_tokens`（prefill
  只调度一次，这次复制必要）、`context_len`、`block_table`；
- `ScheduledDecode`：`seq_id`、`request_id`、最后一个 `input_token`、
  `position`、`context_len`、`block_table`。

`Sequence::to_scheduled_prefill()` / `to_scheduled_decode()` 负责构造；
`input_token` / `position` 保留 `Option`，使调度器状态不一致时仍由
`build_execution_batch` 以原错误信息失败，而不是静默跳过。

`SchedulerOutput`、`ScheduledPrefill`、`ScheduledDecode` 均从 crate 根导出。

## 备选方案

- **把 `Request` 换成 `Arc<Request>`**：clone 变便宜，但生成每步都要
  `Arc::make_mut` 修改 `output_tokens`；只有一个强引用时 `make_mut` 仍需
  复制，多引用时更慢，且改变所有权语义，否决。
- **只克隆 `output_tokens.last()`，其余仍用 `Sequence`**：仍会复制
  `input_tokens`/`logprobs`，没有消除 O(T²)，否决。
- **让 `SchedulerOutput` 借用 `Scheduler`**：需要把 `schedule()` 改成
  返回带生命周期的视图，调用方（引擎）在持有输出时无法再可变借用调度器，
  与现有 `schedule` → `execute` → `update_sequences` 的所有权顺序冲突，否决。
- **只把 block table 改成 `Arc`**：解决的是另一处分配，不解决整序列克隆，
  可以作为后续优化，本次不做。

## 后果

- decode 热路径不再复制 `output_tokens`/`logprobs`/`input_tokens`；每步
  只剩一个 `block_table` Vec 与少量标量。长输出下控制面从 O(T²) 拷贝
  降为 O(T)（块表按块数增长，远小于 token 数）。
- `SchedulerOutput` 是 Rust source-breaking change：直接构造或读取
  `prefill_sequences` / `decode_sequences` 字段的代码需要改用新类型；
  C ABI 不变。
- prefill 的 prompt 仍复制一次，用于构造执行批次；这是执行本身需要的。

## 验证

- `cargo test` 全量：301 passed / 0 failed，覆盖调度器属性测试、
  prefill/decode 批次构造、引擎端到端与 HTTP 集成。
- `cargo clippy --all-targets -- -D warnings`、`cargo fmt --check`：通过。
- 克隆消除由类型系统保证：`SchedulerOutput` 不再持有 `Sequence`/`Request`，
  不存在可复制的输出历史字段；未单独做分配计数或墙钟基准。
