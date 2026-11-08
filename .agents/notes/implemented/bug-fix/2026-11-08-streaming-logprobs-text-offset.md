# Agent Note: 流式 logprobs.text_offset 跨 chunk 累计

Status: implemented

## Problem

非流式响应的 `logprobs_output` 对整段 `logprobs` 从 0 开始累计
`text_offset`，语义正确。流式 SSE 每个 chunk 只携带一个 token 的
`TokenLogprobs`，却复用同一函数：

```rust
logprobs_output(std::slice::from_ref(lp), tokenizer.as_ref(), k)
```

函数内 `let mut offset = 0usize;` 每次调用都从 0 开始，因此所有 chunk 的
`text_offset` 都是 `[0]`。本地实测：

- 流式：`, [0] ; 'b' [0] ; '?' [0] ; 'Y' [0]`
- 非流式：`text_offset=[0, 1, 2, 3]`

同一个 OpenAI 兼容字段在流式与非流式下语义不同，客户端按偏移定位 token
会全部错位。

## Decision

1. 原 `logprobs_output` 保留三参签名，内部转调新增的
   `logprobs_output_at(..., base_offset)`，非流式行为不变。
2. `stream_response` 维护 `emitted_offset`：每个 chunk 以当前偏移构造
   `text_offset`，yield 后累加 `chunk.len()`（字节口径，与引擎 stop 截断一致）。
3. `stream_response_multi` 为每个候选维护 `Vec<usize> emitted_offsets`，
   因为多候选 chunk 交错到达。
4. 扩展 `test_streaming_carries_logprobs`：按 SSE 顺序断言每个内容 chunk 的
   `text_offset[0] == expected_offset`，并累加 chunk 文本字节长度。

## Alternatives considered

- **直接给 `logprobs_output` 增加第四个参数。** 需要改 3 个非流式调用点；
  其中两处会让 rustfmt 重排整个 `match` 分支（约 60 行无关 diff）。用
  三参 wrapper + `_at` 版本把格式化噪声压到最小。否决直接加参。
- **在每个 chunk 里回传完整累积 logprobs。** 违背流式增量语义，显著增大
  响应体，且客户端需要自己去重；否决。
- **按 token 数而非字节长度累计。** `text_offset` 的既有口径是字节偏移
  （多字节 UTF-8 下与 OpenAI 规范对齐），按 token 数会再次不一致；否决。

## Consequences

- 流式每个 chunk 的 `text_offset` 是输出文本中的真实字节偏移，与非流式
  累计语义一致。
- 多候选流按候选独立累计，不因交错到达串位。
- 非流式路径与 API 签名不变。

## Verification

- `cargo test --test server_integration test_streaming_carries_logprobs`：
  通过（1 passed），逐 chunk 断言累计偏移。
- `cargo test --quiet`：300 passed / 0 failed / 1 ignored。
- `cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`：通过。
- 未运行真实 GPU 后端的流式路径；测试使用 CPU 参考执行器。
