# Serving 基线 — RTX 5070 Ti（DEV-02）

## 范围与结论

- 日期 / 硬件：2026-11-10（UTC）；NVIDIA GeForce RTX 5070 Ti 16GB（sm_120），
  driver 616.56，CUDA 13.3.73；发压与被测服务同机（127.0.0.1），无其他租户。
- 被测 commit：paged-serving `09bf4fd`（graph-on 包）/ `5bf0694`（graph-off 包，
  差异仅为新增结果文件）；tiny-llm `d73b68a`；均非 dirty。
- 模型与量化：`qwen2.5-0.5b-instruct-q4_k_m.gguf`（sha256 `74a4da8c…`，491,400,032 B），
  Q4_K_M → 后端 W8A16 权重路径；HF tokenizer（sha256 `c0382117…`）。
- 结论（仅限本矩阵）：
  1. closed c1→c8 吞吐收敛于 ~283-310 tok/s **平台**，TPOT 随并发近似线性
     增长（c1 2.79ms → c8 19.99ms，~7.2× / 8× 并发）——调度层有 batching
     但逐序列执行不摊薄权重读取，与本仓已知架构边界一致。
  2. FFI CUDA Graph on→off 使 decode TPOT 劣化 ~35-45%（c1 2.79→4.34ms，
     c8 19.99→32.30ms），吞吐 ~283-310 → ~194-209 tok/s，确认 Graph 是
     当前 decode 路径的主要开销削减项。
  3. Poisson rate2.0 超出系统容量：成功率 89-95%，拒绝全部为 `http_429`，
     有效吞吐 ~222-235 tok/s；rate0.5/1.0 全成功。
  4. mixed_prefix 与 work 同并发下吞吐接近（差异 <8%）：共享前缀在当前
     实现无可复用缓存路径，属预期，不构成 prefix-cache 收益证据。

## 正确性门控

- 服务启动：`./target/release/paged-serving --serve --backend tiny-llm
  --model-path <q4_k_m.gguf> --port 3000 --tokenizer <tokenizer.json>`，
  `Engine ready` 标记见各包 `server.log`（约 9s 就绪，~4.5 GiB 驻留）。
- 真实后端 canary：`POST /v1/chat/completions` 返回连贯英文输出，
  usage 三字段齐全。
- token 计数：全部 42+12 个 run `token_coverage_pct=100`（usage 字段）。
- 对照后端：同引擎 `TLLM_FFI_CUDA_GRAPHS=0` 见姊妹包
  `../2026-11-10-RTX5070Ti-graph-off/`；llama-server/vLLM 未纳入本次矩阵
  （同负载跨引擎对照留待后续，不作声明）。

## 负载与结果

### Closed-loop

c1/2/4/8 × {work, mixed_prefix} × 3 repeats，warmup 30s，64 请求/run，
max_tokens 128。详见 `summary_table.csv` 与 `ttft_by_concurrency.png` /
`throughput_by_concurrency.png`。全部组合成功率 100%，无 429。

| 组合 | tok/s | TTFT p50 ms | TPOT p50 ms |
|---|---|---|---|
| c1 work | 282.5 | 97.9 | 2.79 |
| c2 work | 297.3 | 193.7 | 5.25 |
| c4 work | 305.4 | 382.4 | 10.19 |
| c8 work | 310.2 | 760.0 | 19.99 |
| c8 mixed_prefix | 284.9 | 900.6 | 21.22 |

### Poisson

rate 0.5/1.0/2.0 req/s × 2 数据集 × 3 repeats，seed 20260904+repeat。
到达率高于闭环容量时成功率如实下降：rate2.0 work 93.2%、mixed_prefix
89.1%；拒绝样本在 `per_request.jsonl` 中带 `error_class=http_429` 及
scheduled/dispatch 时间。SSE chunk 间隔**不是** ITL。

## 对照公平性与限制

- 语义审计：`validate_results.py` 42/42 通过；`audit_provenance.py
  --expect-valid --check-files` 通过（首个完整 provenance 正式包）。
- 收敛：closed 除 c8 work 的 ttft_p95 外全部 converged；Poisson 各组合
  按到达过程本身方差记 non_converged，重复全部保留。
- graph-off 包命令含 `env TLLM_FFI_CUDA_GRAPHS=0`，其余配置与 graph-on
  完全一致；该对照只支持「Graph 开关」单一变量结论。
- 5070 Ti 数字与既有 4090/3060 基线分属不同硬件口径，不拼接优化曲线；
  ~310 tok/s 平台与 4090 的 ~300 接近是巧合性观察，不作跨硬件推论。
- 未测量项：SLO 目标下容量（尾延迟样本量有限）、KV 利用率、
  prefix cache、抢占；7B/8B 扩展未做。
