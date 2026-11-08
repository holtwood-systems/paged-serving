# Agent Note: Qwen2 chat prompt 以 assistant 换行结尾

Status: implemented

## Problem

`qwen2_chat_prompt` 逐条拼接 `<|im_start|>{role}\n{content}<|im_end|>`，最后
追加 `<|im_start|>assistant` 后用 `\n` join。结果 prompt 以
`<|im_start|>assistant` 结尾，没有换行。

仓库自己的 llama.cpp greedy 对齐 oracle
（`tests/tiny_llm_text_e2e.rs:99`）使用的是：

```rust
"<|im_start|>user\nHello, how are you?<|im_end|>\n<|im_start|>assistant\n"
```

即 assistant 引导后带 `\n`。HTTP chat 路径因此从未用与 tiny-llm 数值对齐的
prompt 做过端到端验证；真实模型上的 greedy 输出可能与 oracle 不同。

## Decision

把末尾改为 `<|im_start|>assistant\n`，并同步更新三个单元测试的期望字符串。
注释明确指向 `tests/tiny_llm_text_e2e.rs` 的 oracle，避免再次漂移。

## Alternatives considered

- **改 oracle 去掉 `\n`。** 这会与 Qwen2 官方 chat template 和 llama.cpp
  已验证序列不一致，且需要重新生成 oracle token；否决。
- **在 HTTP 层单独补换行。** 会让 `build_chat_prompt` 的单一来源分裂，
  单元测试与实际请求路径再次不一致；否决。
- **只在 `build_chat_prompt` 的 HuggingFace 分支补。** `qwen2_chat_prompt`
  是模板的实现，补在调用方会让直接调用它的测试/工具绕过；否决。

## Consequences

- HTTP `/v1/chat/completions` 的 HF tokenizer 路径与 llama.cpp oracle 的
  prompt 逐字节一致（assistant 引导行末尾带换行）。
- `SimpleTokenizer` 路径不变（仍是 `role: content` 拼接）。
- 已存历史结果包不受影响；新的 chat 请求会多一个换行 token，prompt_tokens
  计数相应 +1。

## Verification

- `cargo test --lib qwen2_chat_prompt`：2 passed；另跑
  `test_build_chat_prompt_huggingface_uses_qwen2` 覆盖单 user 路径。
- `cargo test --quiet`：300 passed / 0 failed / 1 ignored。
- `cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`：通过。
- 未运行真实 HF tokenizer + GPU 后端的端到端 chat 请求；该路径仍依赖
  `tiny-llm` feature 与真实模型。
