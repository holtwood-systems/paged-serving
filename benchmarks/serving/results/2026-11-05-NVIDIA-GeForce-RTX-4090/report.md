# RTX 4090：paged-serving + tiny-llm tiny-llm 后端 closed-loop 矩阵

## 范围与结论

- 日期：2026-11-05；服务与发压机在同一台机器（loopback HTTP）。
- 硬件与软件：NVIDIA GeForce RTX 4090（49140 MiB）、driver 550.107.02、
  CUDA toolkit 12.8、sm_89（`cuda_archs=89`，tiny-llm 侧 `-arch=native`）。
  CPU 为 Xeon Gold 6530；GPU 时钟、功耗未采集。
- 被测代码：paged-serving `d7e25189f9732a2859d67de2d7fd005dd2b2d6ea`，tiny-llm
  `01f91f93fe5b1921f55b863c8e98e28e9bd4b947`；sweep 启动时两仓均为 clean worktree。
- 模型：`/data/models/qwen2.5-0.5b-instruct-q4_k_m.gguf`，SHA-256
  `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`；
  tokenizer 为 `/data/models/Qwen2.5-0.5B-Instruct/tokenizer.json`（HF 完整词表）。
  **量化口径说明**：GGUF 源文件为 Q4_K_M，但 tiny-llm 加载时把权重反量化到 W8A16
  运行时路径（与 2026-09-07 RTX 3060 包记录口径一致）；本包 `backend_quant` 字段
  按启动参数记录为 `Q4_K_M`（指源文件格式），不能与 W8A16/FP16 包当同量化比较。
- 结论：本包界定上述硬件/提交/模型/负载下的 closed-loop TTFT、TPOT、成功请求率与
  吞吐边界。c2 组合三次重复全部收敛；c1/c4/c8 未收敛，保留全部原始重复用于定位。
  **共享 GPU 干扰**：sweep 期间同机另一进程占用了约 42.7 GiB 显存并执行未知计算
  （nvidia-smi 观测），c4/c8 各有一次 repeat 吞吐降至 31.9/43.6 tok/s（其余 repeat
  分别为 305.3/282.5 tok/s），时间上与外部进程活动吻合；未收敛结论归因于共享环境
  抖动而非引擎必然特性，重测需在独占 GPU 下进行。

本包**不**是任何优化前后对比：paged-serving 与 tiny-llm 相对 3060 包均有新提交，
硬件也从 sm_86 换到 sm_89，跨包只能并列陈列。且当前 FFI 仍只把末层 hidden 批量
后处理（final RMSNorm + LM head + argmax），Transformer layer forward 逐序列执行
——TPOT 随并发近线性劣化（c1 2.67ms → c2 5.14ms → c4 36.9ms → c8 63.2ms）是这一
架构事实的表现，不能解读为 continuous batching 吞吐扩展失败。

## 正确性门控

- 服务以 `--features tiny-llm` 的 release 二进制启动（`LIBRARY_PATH` 指向 CUDA 12.8
  lib64 以补齐 build.rs 未声明的 `-lcudart` 搜索路径），命令：
  `PAGED_SERVING_TINY_LLM_MAX_SEQS=8 ./target/release/paged-serving --serve
  --backend tiny-llm --model-path <gguf> --port 3000 --tokenizer <tokenizer.json>`。
  启动日志确认 `Backend: TinyLlm`、GGUF 解析成功（291 tensors、24 层、hidden 896）。
- 冒烟：loadgen closed c2 × 4 请求，4/4 成功，token coverage 100%。
- 矩阵：12 个 run × 64 测量请求全部成功（768/768，成功率 100%），无 429/timeout/
  连接失败；每 run token coverage 100%（SSE `usage` 字段）。
- TTFT 定义为首个非空文本 SSE chunk 到达延迟；inter-chunk 间隔仅为协议片段间隔，
  **不是** token 级 ITL（协议不提供 token 时间戳，ITL 记为不可用）。
- 对照后端：本包无 llama-server/vLLM 对照；如需跨引擎比较须另行采集。

## 负载与结果

共同参数：`work` 合成数据集（200 条，128–256 token tier）、greedy、
`max_tokens=128`、每组合 64 个测量请求、30 秒同形态预热、3 次重复、
closed-loop 并发 1/2/4/8。服务端 `max_num_seqs=8`。

| 并发 | TTFT p50 (ms) | TTFT p95 (ms, 均值) | TTFT p95 min–max | TPOT p50 (ms) | 吞吐 tok/s (min–max) | 收敛 |
|---|---|---|---|---|---|---|
| c1 | 117.7 | 186.5 | 156.6–204.5 | 2.67 | 234.4 (207.6–280.5) | 未收敛 |
| c2 | 221.5 | 238.1 | 232.6–241.8 | 5.14 | 294.1 (293.3–294.8) | **收敛** |
| c4 | 963.7 | 3217.8 | 466.3–8449.0 | 36.86 | 163.1 (31.9–305.3) | 未收敛 |
| c8 | 1687.2 | 3660.4 | 928.2–8563.3 | 63.24 | 188.9 (43.6–282.5) | 未收敛 |

- c1 未收敛但方向一致（吞吐 207–280 tok/s，相对极差 31%），疑与同一外部干扰相关。
- c2 三次几乎逐点一致（相对极差 0.49%），是本包唯一可作点估计引用的组合。
- c4/c8 的 min repeat 与 max repeat 差近 10 倍，与共享 GPU 外部负载时间吻合；
  按方法论全部原始 repeat 保留于 `per_request.jsonl` / `summary.json` / `audit.json`。
- 图表：`ttft_by_concurrency.png`、`throughput_by_concurrency.png`。

### Poisson

本次未执行 Poisson 模式（`--modes` 默认 closed）；到达率扫描与 SLO 曲线不在本包
范围内，不补造。

## 对照公平性与限制

- 语义审计：`python3 validate_results.py results/2026-11-05-NVIDIA-GeForce-RTX-4090`
  通过（12/12 run，`audit.json` 无 errors/warnings，formal=false 因三组合未收敛）。
- 收敛判定：`(max−min)/mean > 10%` 记 `non_converged`；c1 TTFT p95 极差 25.7%、
  吞吐极差 31.1%；c4/c8 全部指标未收敛。c2 全部指标收敛。
- 未采集：seed 与计划时间表之外的系统状态、GPU 时钟/功耗、外部 GPU 进程的精确
  生命周期；共享环境干扰只能以时间相关性表述，不能作因果断言。
- 未实现功能（KV 利用率采样、prefix cache、抢占、chunked prefill）不推测补全。
- 本包无外推性：不同 GPU/模型/量化/引擎的结论须另行采集。

## 完整复现

```bash
# 构建（tiny-llm 真实后端；build.rs 需要 LIBRARY_PATH 指到 CUDA lib64）
cd paged-serving
LIBRARY_PATH=/usr/local/cuda-12.8/lib64 TINY_LLM_DIR=../tiny-llm/build \
  cargo build --locked --release --features tiny-llm

# 数据集
python3 benchmarks/serving/datasets/synth/gen_synth.py \
  --outdir benchmarks/serving/datasets/synth

# 服务（独占 GPU 时）
PAGED_SERVING_TINY_LLM_MAX_SEQS=8 \
LIBRARY_PATH=/usr/local/cuda-12.8/lib64 LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64 \
./target/release/paged-serving --serve --backend tiny-llm \
  --model-path /data/models/qwen2.5-0.5b-instruct-q4_k_m.gguf \
  --port 3000 --tokenizer /data/models/Qwen2.5-0.5B-Instruct/tokenizer.json

# sweep（PATH 需含 cargo）
cd benchmarks/serving
PATH="$HOME/.cargo/bin:$PATH" ./run_sweep.sh \
  --base-url http://127.0.0.1:3000 --engine paged-serving --model paged-serving \
  --model-path /data/models/qwen2.5-0.5b-instruct-q4_k_m.gguf \
  --backend-quant Q4_K_M \
  --tokenizer /data/models/Qwen2.5-0.5B-Instruct/tokenizer.json \
  --cuda-archs 89 --poisson-seed 20260904

python3 validate_results.py results/2026-11-05-NVIDIA-GeForce-RTX-4090
python3 plots.py results/2026-11-05-NVIDIA-GeForce-RTX-4090
```

## 产物清单

- [x] `metadata.json`（GPU/驱动/toolkit/commit/dirty 标记）
- [x] 每个 run 的 `run_metadata.json`、`per_request.jsonl`、`summary.json`、`stdout.log`
- [x] `summary_table.csv` 与 `ttft_by_concurrency.png`、`throughput_by_concurrency.png`
- [x] `audit.json`（12/12 run 语义校验通过；3 组合 non_converged 如实保留）
- [x] 模型 SHA-256、双仓 commit、硬件/软件版本
- [x] 结论、限制与负结果（共享 GPU 干扰、逐序列 layer forward、无 Poisson）
