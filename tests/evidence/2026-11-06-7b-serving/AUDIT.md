# 7B serving 包审计（2026-11-06）

Status: **invalid / unverified**

## 结论

本包不能作为 7B 性能证据，也不应进入 README、简历、首页或跨引擎比较。
保留目录是为了留下负结果和审计样本，不是承认其中的 TPOT/吞吐数字。

## 触发审计的事实

| 事实 | 数值 | 来源 |
|---|---|---|
| 本包 c1 TPOT p50 | **2.62 ms/token** | `c1-summary.json` |
| tiny-llm 同模型 7B bench TPOT mean | **11.26 ms/token** | `tiny-llm/docs/performance/results/data/2026-11-06-rtx4090-7b-graphs.json` |
| 0.5B 正式包 c1 TPOT p50 | **2.63 ms/token** | `benchmarks/serving/results/2026-11-05-RTX4090-exclusive-gpu/paged-serving_closed_c1_work_r2/summary.json` |

7B 的 c1 TPOT 与 0.5B 包几乎完全重合，却比 tiny-llm 同模型 7B bench
快 4.3 倍；同时低于 7B W8A16 权重读取带宽的理论下限。最可能的解释是
**本次服务实际加载的模型/计算路径与 README 声称的 7B 不一致**，或
**计时/计量边界与 bench 不同**。在模型身份与计时口径被证明前，不能把
2.62 ms/token 当作 7B 结果。

## Provenance 缺口

本包缺少以下可用于排除上述解释的证据：

- `provenance.json`：engine/backend commit、model path + SHA-256 + bytes、
  GGUF 几何（`num_layers` / `hidden_dim` / KV heads / head_dim）、
  tokenizer path + SHA-256、GPU/driver/CUDA、完整启动命令；
- `server.log`：`tinyllm_load` 的模型路径、加载耗时、加载后显存；
- `nvidia-smi` / `cudaMemGetInfo` 原始输出；
- 正式协议要求的 ≥3 repeats + 30 s 预热；本包是 8 请求、0 预热、单轮，
  按 `benchmarks/serving/methodology.md` 只是 canary；
- loadgen 的 `tokenizer` 为 `null`，无法证明服务端 tokenizer 与模型词表一致。

审计命令：

```bash
python3 benchmarks/serving/audit_provenance.py --json \
  tests/evidence/2026-11-06-7b-serving
```

当前输出（预期失败）：

```json
{
  "ok": false,
  "issues": [{
    "code": "missing_provenance",
    "location": "tests/evidence/2026-11-06-7b-serving/provenance.json",
    "message": "缺少 provenance.json；该包不能作为正式结果"
  }]
}
```

## 若要重跑

需要一台稳定的 24GB+ NVIDIA GPU（本机 RTX 3060 Laptop 6GB 不能跑 7B
W8A16），并满足：

1. 用绝对路径加载 7B GGUF，启动命令显式带 `--tokenizer`；
2. 启动时输出 `tinyllm_load` 日志，记录模型路径、SHA-256、bytes、层数、
   hidden size 和加载后显存；
3. 用同一个二进制分别跑 `tiny_llm_bench` 与 `paged-serving`，确认两者
   在相同 prompt/max_tokens 下的 TPOT 可比；
4. 至少 3 repeats + 30 s 预热；每轮保存 raw JSONL 和 summary；
5. 补齐 `provenance.json` 后运行
   `audit_provenance.py --expect-valid --check-files`；
6. 如果审计仍失败，保留负结果，不修改原始 `c*.jsonl` / `c*-summary.json`。

## 仍然成立的部分

- 分页 KV 的 block 计数逻辑（`ceil(tokens/block_size)`）本身可由
  `paged_engine_kv_utilization` 观察；但本包的模型身份未证明，不能把
  这次 5 blocks 的观测写成 7B 的正式证据。
- `/readyz` 竞态是独立观察；当前代码在 `create_engine` 同步完成后才绑定
  监听，理论上不存在该窗口。若重跑仍能复现，应作为单独的 server
  启动/重载 bug 处理，而不是混在 7B 性能结论里。
