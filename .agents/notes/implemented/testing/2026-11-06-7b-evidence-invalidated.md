# Agent Note: 7B serving 证据包因 provenance 缺失与基线矛盾被标记 invalid

Status: implemented

## Problem

2026-11-06 的 `tests/evidence/2026-11-06-7b-serving/` 被写入 README
亮点与 `BENCHMARKS.md` TL;DR，但其 c1 TPOT p50 为 **2.62 ms/token**，
与 tiny-llm 同模型 7B bench 的 **11.26 ms/token** 相差 4.3 倍；同时与
0.5B 正式包的 **2.63 ms/token** 几乎完全重合。该数字还低于 7B W8A16
权重读取带宽的理论下限，不能直接解释为 7B 的真实吞吐。

包内同时缺少：`provenance.json`、`server.log`、模型与 tokenizer 的
SHA-256/bytes、GGUF 几何、GPU/driver/CUDA 原始输出、完整启动命令；
loadgen 的 `tokenizer` 为 `null`。协议上它是 8 请求、0 预热、单轮，
按 `benchmarks/serving/methodology.md` 只是 canary，不是正式性能包。

在此之前，`validate_results.py` 只检查包内数字自洽，不检查“跑的是哪个
模型、哪个后端、哪张卡”。因此一个自洽但身份不明的包可以进入公开索引。

## Decision

1. 将该包标记为 **invalid / unverified**，新增 `AUDIT.md` 记录矛盾、
   缺口和重跑条件；原始 `c*.jsonl` 与 `c*-summary.json` 不修改。
2. 从 `BENCHMARKS.md` TL;DR 移除 7B 行，移到“已失效 / 不可引用结果”；
   README 亮点改为“7B 指标审计”，不再把 2.62 ms/token 当性能成果。
3. 新增 `benchmarks/serving/audit_provenance.py`：正式包必须提供
   `provenance.json` 与非空 `server.log`，覆盖 engine/backend commit、
   model path/SHA-256/bytes/GGUF 几何、tokenizer、GPU/driver/CUDA、
   完整命令和加载观测；缺一项 fail closed。历史 schema-v1 包不回溯。
4. 新正式包发布前运行
   `python3 benchmarks/serving/audit_provenance.py --expect-valid --check-files <dir>`；
   历史包继续用 `validate_results.py`。`main` 在 `create_engine` 后打印
   `Engine ready: backend=..., model_path=..., tokenizer=...`，server.log 可用
   该行证明监听开始前模型加载已返回。

## Alternatives considered

- **保留为“未解异常”并继续放在 TL;DR**：最能保留原有叙事，但公开页面会
  继续把 2.62 ms/token 放在 7B 模型旁边；在模型身份未证明前，这等于把
  异常当结果，否决。
- **直接删除整个 7B 包**：能立刻消除误导，但会丢掉负结果、原始 JSONL 和
  后续重跑的对照样本；保留并标 invalid 更有审计价值，否决删除。
- **立即在稳定 4090 上重跑**：这是最终解法，但当前工作区只有 RTX 3060
  Laptop 6GB，7B W8A16 放不下；Node B 是租用临时节点，可能已回收。先做
  静态审计和门禁，重跑等硬件确认，暂缓。
- **新增 `model_tokens_generated` 计数器**：`EngineMetrics.total_tokens_generated`
  已经按 `req.output_tokens.len()` 统计模型 token；再建一个计数器是重复。
  当前矛盾更像模型身份/计时边界，不是 token 计数，否决。

## Consequences

- 公开 README/BENCHMARKS 不再把 7B canary 的 TPOT/吞吐当作成果；KV 公式
  的“5 blocks”观测保留在包内，但在模型身份被证明前不作为 7B 正式证据。
- 新正式包多一个 `provenance.json` 和 `server.log` 的落盘要求；历史包
  不受影响，CI 只新增标准库单元测试，不需要 GPU。
- 没有改变 runtime 行为；`/readyz` 的独立观察不在本决策内。当前
  `create_engine` 在绑定监听前同步完成 `tinyllm_load`，理论上没有
  “加载中返回 200”的窗口；若重跑仍复现，另开 server 启动 bug。
- 代价是首页少了一个“7B 真实服务”亮点；收益是数字可防守，且下一次
  7B 重跑有明确的 provenance 清单。

## Verification

- `python3 -m unittest benchmarks/serving/test_audit_provenance.py -v`：
  9 项通过。
- `python3 benchmarks/serving/audit_provenance.py --json tests/evidence/2026-11-06-7b-serving`
  退出 1，问题码 `missing_provenance`。
- `python3 -m unittest discover -s benchmarks/serving -p 'test_*.py' -v`
  覆盖既有结果语义测试与新增 provenance 测试。
- `npm run verify-notes` 通过。
