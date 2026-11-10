# DEV-13 批量 decode 端到端 A/B — RTX 5070 Ti

## 范围与结论

- 日期 / 硬件：2026-11-10 ~ 2026-11-11（UTC）；NVIDIA GeForce RTX 5070 Ti
  16GB（sm_120），driver 616.56，CUDA 13.3.73；发压与被测同机（127.0.0.1:3111）。
- 被测 commit：paged-serving `3ab35ad`；tiny-llm v1 臂 `4016c6c`（批量门
  {2,4,8}）、graph-batch-v2 臂 `de17ede`（门收窄为 {4,8}）。**dirty
  provenance 如实披露**：v1 运行时 paged-serving worktree 含未入库
  dev13_ab.sh 与结果文件（不影响被测二进制，src/tests 无 diff）；v2 运行时
  ffi.cpp 收窄编辑未提交，`de17ede` 提交后源码可完整重建。
- 模型与量化：`qwen2.5-0.5b-instruct-q4_k_m.gguf`（sha256 `74a4da8c…`），
  Q4_K_M → W8A16；HF tokenizer（sha256 `c0382117…`）。
- 结论一句话：**decode 行批量在 closed 并发 ≥4 时显著优于当前默认
  graph-scalar（c8 吞吐 +24.8%、TPOT −26.2%），B=2 时反而劣于 graph——
  因此 FFI 批量门收窄为 {4,8}，`TLLM_BATCH_DECODE` 维持 opt-in 默认关闭。**

## 臂定义与结果

| 臂 | env | 说明 |
|---|---|---|
| eager-scalar | `TLLM_FFI_CUDA_GRAPHS=0` | 机制对照下界 |
| eager-batch | `TLLM_FFI_CUDA_GRAPHS=0` + `TLLM_BATCH_DECODE=1` | 批量收益隔离 |
| graph-scalar | （默认） | **当前生产默认，强基线** |
| graph-batch | `TLLM_BATCH_DECODE=1`（{2,4,8} 门） | v1，后被 v2 修正 |
| graph-batch-v2 | `TLLM_BATCH_DECODE=1`（{4,8} 门） | 决策后形态 |

吞吐（output tok/s，3 重复中位数；全部 64/64 成功）：

| 并发 | eager-scalar | eager-batch | graph-scalar | graph-batch v1 | graph-batch v2 |
|---|---|---|---|---|---|
| c1 | 189.9 | 194.5 | 284.3 | 284.6 | —¹ |
| c2 | 202.5 | 261.0 | 296.4 | **262.2 ↓11.5%** | 296.4 |
| c4 | 206.1 | 341.9 | 306.4 | 341.3 | 338.9 |
| c8 | 207.7 | 388.2 | 310.6 | 384.1 | 387.6 |

TPOT p50（ms，中位数）：

| 并发 | eager-scalar | eager-batch | graph-scalar | graph-batch v1 | graph-batch v2 |
|---|---|---|---|---|---|
| c1 | 4.43 | 4.41 | 2.77 | 2.76 | —¹ |
| c2 | 8.43 | 6.19 | 5.28 | 5.97 | 5.28 |
| c4 | 16.55 | 8.79 | 10.16 | 8.80 | 8.87 |
| c8 | 32.57 | 14.78 | 19.97 | 15.07 | 14.73 |

¹ v2 未重跑 c1：B=1 恒回落逐序列路径，与 graph-scalar 同一执行路径。

跨臂对比图见 `ab_comparison.png`，全量数字见 `ab_summary.csv`，
每臂 `summary_table.csv`/`throughput_by_concurrency.png`/`audit.json` 齐备。

## 机制解读

1. **批量收益是真实的**：eager 域内 B∈{4,8} 行批量相对逐序列
   c4 +66%、c8 +87%（341.9/206.1、388.2/207.7）——层主体 GEMM 的
   权重读取被摊薄（DEV-11 small-M kernel）。
2. **但 graph 省下的是另一份开销**：graph-scalar 相对 eager-scalar
   c1 +50%、c8 +50%。B=2 时 eager 批量摊薄收益 < graph 逐 kernel
   launch 消除收益，v1 {2,4,8} 门在 c2 造成 −11.5% 回退——故收窄。
3. **{4,8} 门使批量路径对默认基线严格不劣**：c1/c2 逐位走原路径
   （数值上持平），c4/c8 批量全胜。

## 默认值决策（按 DEV-13 候选门槛逐项）

| 门槛 | 结果 |
|---|---|
| c4/c8 ≥10% TPOT 或吞吐改善 | ✓ TPOT −12.7%/−26.2%，吞吐 +10.6%/+24.8% |
| 改善须超重复噪声 | ✓ 重复极差 ~1–2%，改善 10–26% |
| c1 TPOT 回退 ≤5% | ✓ 0%（同路径） |
| TTFT p95 无 >10% 回退 | ✓ 全部单元格 |Δ|≲2% |
| 无新增正确性错误 | ✓ 差分逐位一致、258 测试、sanitizer 净 |
| 重复收敛门 | ✗ ttft_p95 non_converged：eager-scalar c8、eager-batch c4、graph-batch c4、graph-batch-v2 c8 |

**决策：维持 opt-in（默认关闭）。** 收窄后各并发不劣于基线，但证据
仅覆盖单模型（0.5B）/单负载（work）/单硬件，且收敛门存在未过单元格
（TTFT 尾部队列噪声，baseline 臂同样出现）——按规格"门槛未满足保持
opt-in，记录有用形状与代价"执行。

**推荐配置**：closed 并发 ≥4 的负载启用 `TLLM_BATCH_DECODE=1` 严格
不劣于默认且显著更优；低并发（≤2）下 env var 无效果（自动回落）。

**回滚**：删除 env var 即恢复默认路径，默认代码路径未改动。

## 追记：DEV-14 批量 graph 分桶（graph-batch-v3）

tiny-llm `136c0d0` 实现 per-B graph 捕获（B∈{2,4,8} 每 B 一张，
行差异全走 device 数据），本包追加 v3 实测：

| 并发 | graph-scalar | graph-batch-v2（eager 批量） | graph-batch-v3（批量 graph） | v3 Δ |
|---|---|---|---|---|
| c1 | 284.3 | — | 282.4 | 0%（同路径） |
| c2 | 296.4 | 296.4 | **363.4** | **+22.6%** |
| c4 | 306.4 | 338.9 | **410.9** | **+34.1%** |
| c8 | 310.6 | 387.6 | **456.3** | **+46.9%** |

TPOT p50：c2 5.28→4.01（−24%）、c4 10.16→6.83（−32.8%）、
c8 19.97→11.67（−41.6%）；TTFT p95 无 >10% 回退；12 run 全部
收敛门通过；重复极差 <1%。

**默认值决策翻正**：既定门槛（≥10% 收益、超噪声、c1 回退≤5%、
无 TTFT 回退、无正确性错误、收敛门）全部满足 →
`TLLM_BATCH_DECODE` 已改为**默认启用**（=0 opt-out 回滚）。
`graph-batch-v3-default/` 为无 env 默认行为抽查（c8 453.5 tok/s，
与 env=1 一致）。B=3/5/6/7 与 prefill/logprobs/策略 2 行仍回落。

## 已知限制

- 单模型（Qwen2.5-0.5B）/单负载（work 数据集）/单硬件（5070 Ti）——
  其他 GQA 几何、负载形状、GPU 未覆盖。
- B=3/5/6/7 行仍整体回落（kernel 模板未实例化，混合行数下批量间歇命中）。
- 批量路径为 eager；非批量行才走 graph。按 B 分桶捕获（B∈{4,8} 各一张
  graph）为 DEV-14 候选，可同时覆盖 B=2 并进一步压缩 launch 开销。
- 批量内任一行 kernel/device 级失败为调用级失败（ABI v3 语义内）。
- v1 四臂 provenance 为 dirty（脚本/结果未入库），已如实披露；
  语义校验与 provenance 审计逐臂通过（`validate_results.py` 12/12 ×4 +
  9/9，`audit_provenance.py --check-files` 5/5）。
