# 7B serving 实验包（2026-11-06）

> **Status: invalid / unverified（2026-11-06 审计）**
>
> 本包的 c1 TPOT 2.62 ms/token 与 tiny-llm 同模型 7B bench 11.26 ms/token
> 矛盾，且与 0.5B 正式包的 2.63 ms/token 几乎重合；包内缺少 server log、
> 模型/ tokenizer hash、GPU 元数据和正式重复/预热。按本仓正式门槛，这不是
> 性能结果。保留为负结果和审计样本，不用于 README、简历或跨引擎比较。
> 详见 [`AUDIT.md`](AUDIT.md)。

以下保留本次 canary 的原始观察与未解异常，供审计和重跑对照；
它们不是可引用的 7B 性能结论，包状态以上方 invalid/unverified 为准。

## 配置

- `paged-serving --serve --backend tiny-llm --model-path qwen2.5-7b-q4_k_m.gguf`
  （sha256 `65b8fcd9…1423`），`PAGED_SERVING_TINY_LLM_MAX_SEQS=8`
- KV 池：block_size=16 × max_num_blocks=1024 = 16384 tokens 容量
- RTX 4090 / CUDA 12.8.61 / driver 550.107.02
- 背景干扰：外部租户进程占 ~3.5GiB

## 原始观察（不可引用）

### KV 分页公式观测（面试题"KV Cache 占多大显存"）

prompt 14 tok + 64 生成 = 78 tok → `paged_engine_kv_utilization` 峰值
0.0049 ≈ **5 blocks** = `ceil(78/16)`，与分页公式精确一致；
末块内碎片 2 tok（2.6%）。分页把外部碎片清零、代价是 ≤block_size-1
的内部碎片——本次观测；模型身份与 tokenizer 未证明，不能写成 7B 的正式证据。

### closed-loop 并发扫描（max_tokens=64，work.jsonl）

> 下表只是本次 canary 的原始观测，不是可引用的 7B 性能结果；单轮、0 预热，
> 且模型/计时 provenance 未补齐。

| 并发 | TTFT p50 | TPOT p50 | 聚合吞吐 | 备注 |
|---|---|---|---|---|
| c1 | 129 ms | 2.62 ms | 222 tok/s | 见下方异常 |
| c4 | 395 ms | 10.27 ms | 242 tok/s | |
| c8 | 831 ms | 20.70 ms | 242 tok/s | 吞吐在 c4 饱和 |

loadgen 指标见 `c*-summary.json` / `c*.jsonl`。

## 未解异常：单流 TPOT 低于权重读取带宽下限

- **现象**：单请求 wall 计时 256tok/714ms ≈ **2.8–3.2 ms/token**，
  输出文本真实连贯（"当然可以，我很乐意帮助您…"）。
- **矛盾**：7B W8A16 权重 ~7.7GB，单步全量 GEMV 读取在 4090 峰值
  ~1008GB/s 下的理论下限 ≈7.6ms；2.8ms 低于下限 2.7×。
- **已排除的解释**：
  - 不是回退 CPU/mock：`tiny_llm_ffi` 无 stub，GGUF 解析日志真实，
    `cfg(feature="tiny-llm")` 缺失会直接报错而非回退；
  - 不是输出造假：`FFITest.PagedKVStrategyMatchesContiguous`
    **在 7B 上通过**（paged 与连续 KV 逐 token 一致），"量子纠缠"
    复读输出与 standalone `tiny_llm_demo` **逐字一致**（temp=0
    真实模型行为，非路径损坏）；
  - 不是预热窗口：权重加载完成后（GPU 18GB 稳定）复测仍 ~3.2ms/tok。
- **候选方向**（未验证）：paged decode 每步实际计算的权重子集
  小于全量（如 dispatch 分片/懒层）、`st.position`/is_prefill 语义
  使单步处理多于 1 token、或引擎在 SSE 层缓冲使 wall 计时失真。
- **下一步**：per-step kernel 级计时（kernel_bench 对 paged attention
  路径）或在 FFI 层加 `TLLM_FFI_DEBUG` 的 step 计时探针；
  对比 `paged_engine_tokens_generated_total` 增量与 wall。

## readiness 竞态（独立观察）

服务就绪探针 `/readyz` 在权重仍在后台上传期间即返回 200
（实测加载 ~5min：GPU 3.5→18GB 爬升期请求已被受理）。
此期间生成的 token 基于部分权重，属"预热期输出不可信"缺陷，
应改为加载完成才 ready 或预热期 503。

## 文件

- `c{1,4,8}-summary.json` — loadgen summary（schema v1）
- `c{1,4,8}.jsonl` — per-request 明细（含 inter_chunk 时序）
