# Serving 基准汇总

> 本文件是评测结果的**索引入口**：每个数字都绑定硬件、模型 SHA-256、双仓 commit
> 与原始逐请求记录；口径、负载与审计工具见
> [`benchmarks/serving/README.md`](benchmarks/serving/README.md) 与
> [`benchmarks/serving/methodology.md`](benchmarks/serving/methodology.md)。
> 不在本文件新增任何没有归档结果包支撑的数字。

## TL;DR 数字表（点击进结果包看前提）

| 日期 | 环境 | 模型 | 关键结果 | 结果包 |
|---|---|---|---|---|
| 2026-11-05 | RTX 4090 独占 | 0.5B Q4_K_M→W8A16 | closed c1–c8 全收敛：吞吐 284.8→302.3 tok/s、TPOT 2.63→20.38 ms、TTFT p95 124.5→876.6 ms；768/768 成功 | [`results/2026-11-05-RTX4090-exclusive-gpu/`](benchmarks/serving/results/2026-11-05-RTX4090-exclusive-gpu/report.md) |
| 2026-11-05 | RTX 4090（共享，外部租户占卡） | 同上、同二进制同种子 | c1/c4/c8 未收敛；c8 TPOT 63.2 ms（干扰放大约 3×）、吞吐均值 188.9 tok/s | [`results/2026-11-05-NVIDIA-GeForce-RTX-4090/`](benchmarks/serving/results/2026-11-05-NVIDIA-GeForce-RTX-4090/) |
| 2026-11-06 | RTX 4090（0.5B） | 0.5B Q4_K_M→W8A16 | 故障注入 5/5：flood 64 并发 → 200×8+429×56 零 5xx；SSE RST / 慢消费者 / 泊松混合流量资源回零 | [`tests/evidence/2026-11-06-fault-injection/`](tests/evidence/2026-11-06-fault-injection/README.md) |
| 2026-09-07 | RTX 3060 Laptop 6GB | 0.5B Q4_K_M→W8A16 | 批量末端后处理后 21-run：closed 64/64 成功；Poisson 0.64 / 1.28 req/s 分别 9 / 74 个 429；c1/c4 过收敛门，c2/c8 与 Poisson 未收敛（保留） | [`results/2026-09-07-RTX3060Laptop-paged-serving-p2-batch-postprocess-streaming/`](benchmarks/serving/results/2026-09-07-RTX3060Laptop-paged-serving-p2-batch-postprocess-streaming/) |
| 2026-09-04 | RTX 3060 Laptop 6GB | 0.5B | 首份真实 CUDA serving：closed c1–8 约 82 tok/s 且未随并发扩展；Poisson 1.0x 饱和出现 429 | [`results/2026-09-04-RTX3060Laptop-paged-serving/`](benchmarks/serving/results/2026-09-04-RTX3060Laptop-paged-serving/) |

## 已失效 / 不可引用结果

| 日期 | 包 | 状态 | 原因 | 审计 |
|---|---|---|---|---|
| 2026-11-06 | 7B serving canary | **invalid / unverified** | c1 TPOT 2.62 ms/token 与 tiny-llm 同模型 7B bench 11.26 ms/token 矛盾；c1 与 0.5B 正式包 2.63 ms/token 几乎重合；8 请求、0 预热、单轮；缺 `provenance.json` / server log / 模型与 tokenizer hash | [AUDIT.md](tests/evidence/2026-11-06-7b-serving/AUDIT.md) |

这些包保留为负结果或审计样本，不进入 TL;DR、README 亮点、简历或跨引擎比较。

历史包（P1/P2 中间代、stream canary、batch-postprocess canary）保留在
[`benchmarks/serving/results/`](benchmarks/serving/results/) 下，仅作可追溯边界证据，
不用于当前代码的性能表述。

## 复现与审计

```bash
# 负载与 sweeps（closed-loop / Poisson，固定 seed）
bash benchmarks/serving/run_sweep.sh

# 结果校验：重算 JSONL/summary、10% 收敛门、语义校验、audit.json
python3 benchmarks/serving/validate_results.py <results-dir>

# Provenance 审计：新正式包必须通过；历史 schema-v1 包不回溯
python3 benchmarks/serving/audit_provenance.py --expect-valid <results-dir>
```

口径要点（详见 methodology）：

- TTFT = 首个**非空文本** SSE 片段；TPOT 来自 per-request 统计；inter-chunk 间隔
  不是 token 级 ITL；
- 正式校验要求 ≥3 次重复 + 30 s 预热；指标 `(max-min)/mean` >10% 标
  `non_converged`，负结果原样保留、不隐藏；
- 每个结果包绑定模型 SHA-256、双仓 commit 与逐请求 JSONL。

## 进行中（无结果，先占位、不填数字）

| 实验 | 状态 | 占位 |
|---|---|---|
| vLLM 0.10.2 + Qwen2.5-32B-AWQ 消融矩阵（CUDA Graph / prefix caching / chunked prefill / FP8 KV / TP2） | **实验进行中**，数据未出 | 表格与结论留空；跑完并审计后回填，绑定 commit / 模型 SHA / 硬件 / 负载参数 |
| paged-serving ↔ llama-server ↔ vLLM 同负载三后端对照 | 计划中（见 ROADMAP 阶段 3） | 未开始 |
| tiny-llm 融合 batch decode（逐层 batch compute）后重采矩阵 | 计划中（见 ROADMAP P2） | 当前架构仍是逐序列 layer forward，已有数字不能表述为 fused compute batch 的收益 |

## 已知边界（引用数字前必读）

- 4090 独占包 sweep 期间同机有 ~3–10 核 nvcc 编译负载（已声明），TTFT 口径可能受
  轻微影响；GPU 侧无竞争。
- 2026-11-06 7B serving canary 已标记 **invalid/unverified**：c1 TPOT
  2.62 ms/token 与 tiny-llm 同模型 7B bench 11.26 ms/token 矛盾，且与 0.5B
  正式包 2.63 ms/token 几乎重合；包内缺 provenance/server log。不得引用其
  TPOT/吞吐；详见 [AUDIT.md](tests/evidence/2026-11-06-7b-serving/AUDIT.md)。
- 跨硬件（3060 ↔ 4090）、跨量化（Q4_K_M 源 ↔ W8A16 运行时）不作同口径比较。
- 聚合吞吐在 c1 已接近饱和（逐序列 layer forward 架构的直接表现），不能解读为
  continuous batching 的扩展能力。
