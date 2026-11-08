# Agent Note: stop 序列检测增量化

Status: implemented

## 问题

`InferenceEngine::apply_stop_sequences` 每步对所有配置了 stop 且本步生成
token 的请求调用 `tokenizer.try_decode(&req.output_tokens)`，把已生成的全部
输出重新解码一遍；命中时 `tokens_before_char` 又逐 token 解码一次。第 t 步
做 O(t) tokenizer 工作，整段生成为 O(T²)。

`engine_loop` 是唯一的后台任务，所有请求的 prefill/decode 与 stop 检测都在
其中同步执行，因此长生成（T=2048、batch=32）会持续抬高同一实例所有请求的
TTFT/TPOT。

## 决定

给每个请求维护 `StopScanState`：

- `decoder`：与输出流同类型的增量解码器，只把**新 token** 喂进去；
- `text`：增量解码出的完整输出文本（拼接结果与一次性 decode 一致）；
- `scanned`：已搜索过的字节数；下次只从
  `scanned - (最长 stop 长度 - 1)` 开始搜索，覆盖跨 chunk 边界的命中；
- `max_stop_len`：回退窗口长度；
- 命中时才调用一次现有的 `tokens_before_char` 做 token 边界换算，保持截断
  语义与旧实现逐字节一致。

状态在请求首次出现在 `generated` 时惰性创建，在 stop 命中、请求完成/取消/
失败（`collect_completed_requests`）时删除。

## 备选方案

- **缓存完整文本但每步仍全量 decode**：没有解决 O(T²) 的 decode 本身，否决。
- **只搜索本步新增文本**：会漏掉跨 token 边界的 stop（例如 stop="AB"、本步
  只生成 "B"），必须保留回退窗口。
- **复用输出流的 decoder**：`apply_stop_sequences` 必须在文本推送给客户端
  **之前**执行；复用输出 decoder 要么先推送（命中后无法撤回已发送 chunk），
  要么把 decoder 的内部缓冲暴露出来，破坏封装，否决。
- **把 stop 编码成 token 序列做整数匹配**：stop 是任意文本，BPE 下同一文本
  可能有多种 token 切分，且大小写/空白规范化会引入差异；文本匹配才是当前
  契约，否决。

## 后果

- 正常路径每步只做 O(新 token) 的 tokenizer 工作，整段生成降为 O(T)；
  stop 命中时多做一次 O(T) 的 `tokens_before_char`（旧实现本来也要做）。
- 每请求多保留一份输出文本与一个增量解码器；与 `Request.output_tokens`
  同阶，不改变内存复杂度。
- 增量解码器在字节序列不完整时会暂存尾部（`step_decode_stream` 遇到
  `�` 不输出）。完整 stop 序列不可能结束在半个 UTF-8 码点内，因此不会漏检；
  代价是这类尾部会晚一步参与匹配。
- stop 恰好包含 U+FFFD 的极端配置会延迟到尾部冲刷时命中；这类 stop 不在
  实际使用范围内。

## 验证

- 新增 `test_stop_scan_decodes_incrementally_not_quadratically`：统计
  `try_decode` 调用次数，20 步生成 + 不命中 stop 时 ≤4 次（旧实现约
  `20×21/2 = 210` 次）。
- `cargo test --lib engine::tests::test_stop`：5 passed，覆盖普通命中、
  max_tokens 边界命中、Unicode 字节偏移、未命中。
- `cargo test` 全量：301 passed / 0 failed；`cargo fmt --check`、
  `cargo clippy --all-targets -- -D warnings`：通过。
- 未在真实 HuggingFace tokenizer + GPU 后端上做长生成压测；本项只改变
  控制面 tokenizer 调用次数，GPU 执行路径未动。
