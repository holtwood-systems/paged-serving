# 2026-11-06 serving 故障注入证据（RTX 4090，真实 tiny-llm 后端）

对真实运行服务施加客户端侧网络故障，用 `/metrics` 断言资源恢复与
契约行为。工具：`tests/fault_injection/inject.py`（Python 标准库）。
证据：`inject-20261106T021514Z.json`（逐实验 before/after 指标快照 +
断言明细）。

## 环境

| 项 | 值 |
|---|---|
| GPU | NVIDIA GeForce RTX 4090，驱动 550.107.02 |
| 引擎 | paged-serving 本仓提交（见 run 时 `git log`），`--features tiny-llm` release |
| 后端 | tiny-llm（Qwen2.5-0.5B-Instruct Q4_K_M GGUF，sha256 `74a4da8c…93d7a9db`，W8A16 运行时） |
| 服务配置 | `PAGED_SERVING_TINY_LLM_MAX_SEQS=8`（后端容量 8） |
| 事件通道 | 默认 `event_channel_capacity=64`（E2 溢出的前提） |

启动：

```bash
PAGED_SERVING_TINY_LLM_MAX_SEQS=8 \
LIBRARY_PATH=/usr/local/cuda-12.8/lib64 LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64 \
./target/release/paged-serving --serve --backend tiny-llm \
  --model-path /data/models/qwen2.5-0.5b-instruct-q4_k_m.gguf \
  --port 3000 --tokenizer /data/models/Qwen2.5-0.5B-Instruct/tokenizer.json
python3 tests/fault_injection/inject.py --base-url http://127.0.0.1:3000 \
  --server-pid <pid> --out tests/evidence/2026-11-06-fault-injection/inject-<ts>.json
```

## 结果（4/4 实验 pass，全部断言绿）

| 实验 | 故障 | 断言与观察 |
|---|---|---|
| E1 mid_sse_rst | 流式读到 2 个 SSE 事件后 SO_LINGER RST 断连 | `cancelled_requests` +1；inflight/active/kv 全回零；`/healthz` 200；后续请求正常返回 |
| E2 slow_consumer | 读 1 个事件后停顿 25s（event mailbox 容量 64 → 溢出） | 停顿期间并发探针正常（**慢消费者不拖垮其他请求**）；RSS 增长 26.8 MiB（<512 上限，**有界**）；溢出后流被终止（连接关闭）；资源回零 |
| E3 flood_admission | 64 并发非流式短请求（后端容量 8） | **200×8 + 429×56，零 5xx、零挂起**；`requests_total` 增量恰为 64；资源回零 |
| E4 nonstream_abort | 准入前（50ms）与生成中（300ms）断连 | 生成中断连 `cancelled` +2；两个时点资源均回零；准入前断连无终态计数（未进引擎，不泄漏） |

## 本证据抓出的真实缺陷与修复（同批提交）

E3 首跑（64 并发）时全部返回 **500 internal_error**
（`Kernel 启动失败: Batch size N exceeds max 8`）：`PAGED_SERVING_TINY_LLM_MAX_SEQS`
只约束后端 executor，调度器 `max_num_seqs`（默认 256）/`max_batch_size`
（默认 32）与之无联动——过载在 batch 组层变成 500，而非准入 429。

修复：`create_engine` 的 TinyLlm 分支在构造调度器前把两个上限夹到
`backend_max_seqs()`（见 `.agents/notes/implemented/bug-fix/2026-11-06-tiny-llm-admission-not-clamped.md`）。
修复后同场景 200×8 + 429×56。**这是本证据包最重要的产出：故障注入
直接定位了准入契约缺陷。**

## 边界

- 单机回环网络，未覆盖真实网络抖动/丢包（netem 未使用）；
- 慢消费者只测事件通道溢出路径，未测超大 token 流的持续背压梯度；
- 429 的 Retry-After 数值语义未逐档验证；
- 指标断言依赖当前 `/metrics` 字段语义，上游变更需同步调整。
