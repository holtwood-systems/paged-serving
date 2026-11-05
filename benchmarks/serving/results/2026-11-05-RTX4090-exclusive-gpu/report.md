# RTX 4090 独占：paged-serving + tiny-llm closed-loop 收敛矩阵

## 范围与结论

- 日期：2026-11-05；服务与发压机同机（loopback HTTP）。
- 与 `2026-11-05-NVIDIA-GeForce-RTX-4090` 包的关系：**同一二进制、同一参数、
  同一随机种子的复测**。首包采集时同机另一进程占用了约 42.7 GiB 显存执行
  未知计算，c1/c4/c8 未收敛；本包在 GPU 完全空闲后重测，**4 档并发全部
  收敛**（各指标 `(max-min)/mean` 均 <4%），首包的 non_converged 归因于
  共享环境干扰得到证实。两包均保留，互为对照：共享环境干扰上限见首包。
- 硬件与软件：RTX 4090（49140 MiB）、driver 550.107.02、CUDA toolkit 12.8、
  sm_89。CPU Xeon Gold 6530。**并发 CPU 负载声明**：sweep 期间同机另有
  ~3-10 核的 nvcc 编译任务（sgl-kernel 源码构建），对本包 TTFT 口径可能有
  轻微影响；GPU 侧无竞争。
- 被测代码：paged-serving `d7e25189f9732a2859d67de2d7fd005dd2b2d6ea`，
  tiny-llm `01f91f93fe5b1921f55b863c8e98e28e9bd4b947`，启动时 clean。
- 模型：`qwen2.5-0.5b-instruct-q4_k_m.gguf`，SHA-256
  `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`；
  tokenizer 为 HF 完整词表 `Qwen2.5-0.5B-Instruct/tokenizer.json`。
  `backend_quant=Q4_K_M` 指 GGUF 源格式；tiny-llm 加载时反量化到 W8A16
  运行时路径（与 3060 包同口径），不能当同量化跨包比较。
- 结论：本配置下引擎聚合吞吐在 ~285–302 tok/s 饱和（c1 已接近上限，c8
  提升 <7%）；TPOT 随并发近线性增长（2.63→20.38ms）；TTFT p95 随并发
  近线性排队（124→877ms）。这些是逐序列 layer forward + 批量末端后处理
  架构的直接表现，不能解读为 continuous batching 扩展能力。

## 正确性门控

- 启动命令、后端声明、量化口径：与共享 GPU 包完全相同（见该包 report.md
  「完整复现」节），冒烟 4/4、矩阵 768/768 成功、token coverage 100%。
- TTFT/ITL 定义同前：inter-chunk 间隔不是 token 级 ITL。

## 负载与结果

共同参数：`work` 数据集 200 条、greedy、max_tokens=128、每组合 64 请求、
30s 预热、3 repeats、closed c1/c2/c4/c8、`PAGED_SERVING_TINY_LLM_MAX_SEQS=8`。

| 并发 | TTFT p50 | TTFT p95（min–max） | TPOT p50 | 吞吐 tok/s（min–max） | 收敛 |
|---|---|---|---|---|---|
| c1 | 115.0 | 124.5 | 2.63 | 284.8（284.4–285.1） | 收敛 |
| c2 | 218.6 | 250.0 | 5.13 | 292.9（291.5–293.7） | 收敛 |
| c4 | 401.6 | 457.1 | 10.12 | 300.8（297.2–304.2） | 收敛 |
| c8 | 800.8 | 876.6 | 20.38 | 302.3（294.4–306.8） | 收敛 |

- 全部 12 个 run 语义校验通过；图表 `ttft_by_concurrency.png`、
  `throughput_by_concurrency.png`。
- 与共享包对照：c8 TPOT 63.2ms→20.4ms（外部干扰约放大 3 倍）、c4 TTFT p95
  3218ms→457ms；吞吐 c8 均值 189→302 tok/s。差异是环境干扰量级，
  不是引擎改动效果（同一二进制）。

### Poisson

未执行（同共享包口径）。

## 对照公平性与限制

- `validate_results.py` 12/12 通过；`audit.json` 无 errors。
- 未采集 GPU 时钟/功耗；CPU 侧有并发编译负载（已声明）。
- 未实现功能不推测补全；跨硬件/跨量化不作同口径比较。

## 产物清单

- [x] `metadata.json`、12 个 run 全套逐请求产物、`summary_table.csv`、图表、
  `audit.json`、模型 SHA-256、双仓 commit
- [x] 复测动机与共享包对照说明
