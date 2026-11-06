# Agent Note: serving readiness 观察与 7B TPOT 异常待复核

Status: proposed

## Problem

2026-11-06 的 7B serving canary（`tests/evidence/2026-11-06-7b-serving/`）
记录了两个现象：

1. `/readyz` 在 GGUF 权重加载期间返回 200，加载窗口内请求已受理并产生
   基于部分权重的输出。
2. 单流 TPOT 约 2.8–3.2 ms/token，低于 7B W8A16 权重读取带宽的理论下限
   （约 7.6 ms）。

同日复核发现该包缺少 `provenance.json`、server log、模型/tokenizer hash、
GGUF 几何、GPU/driver/CUDA 和完整命令；c1 TPOT 2.62 ms/token 与 0.5B
正式包的 2.63 ms/token 几乎完全重合，却比 tiny-llm 同模型 7B bench 的
11.26 ms/token 快 4.3 倍。该包已被标记 **invalid/unverified**，见
[`AUDIT.md`](../../../../tests/evidence/2026-11-06-7b-serving/AUDIT.md)。

因此两个现象都不能按“已复现 bug”处理：

- 当前代码路径在 `main` 中先同步执行 `create_engine`（含
  `tinyllm_load`），成功后才 `TcpListener::bind`；理论上不存在
  “监听已开、权重仍在后台加载”的窗口。原始观察更可能来自旧进程、
  端口复用或错误的结果包记录。
- 2.8 ms/token 在模型身份未证明前不能归因于 paged decode 的权重节省；
  它也可能是服务实际加载了更小模型、计时边界不同，或单步处理了多个
  token 但被计成单 token。

## Proposal

先做证据复核，再决定是否改 runtime：

1. 用稳定 24GB+ GPU 重跑 7B，启动命令显式带 `--tokenizer`，记录
   `tinyllm_load` 日志、模型/tokenizer SHA-256、GGUF 几何和加载后显存；
2. 同一二进制分别跑 `tiny_llm_bench` 与 `paged-serving`，比较相同
   prompt/max_tokens 下的 TPOT；
3. 在 FFI 层加可选的 `TLLM_FFI_STEP_TIMING`，输出每步
   `num_sequences`、prefill/decode、单步耗时和实际处理的 token 数；
4. 用 `total_tokens_generated` 增量与 wall 交叉验证，确认不是 token
   计数或 SSE 缓冲造成的假象；
5. 只有重跑仍复现 `/readyz` 窗口时，才引入显式
   `EngineState::{Loading, Ready, Draining}`；否则把原始观察标为
   不可复现，不写推测性状态机。

## Alternatives considered

- **按原观察直接实现 `/readyz` 状态机**：能覆盖未来异步加载，但当前
  代码没有该窗口；在证据未复现前加状态机会制造无用复杂度和新的
  并发面，否决。
- **直接把 2.8 ms/token 当作 paged decode 的优势**：数字与 0.5B 包
  重合，且缺少模型身份；这会重复“漂亮数字先行”的错误，否决。
- **删除 7B 包和相关 note**：会丢掉负结果与重跑清单；保留 invalid
  包并记录待复核项更有价值，否决删除。
- **只加 `model_tokens_generated` 计数器**：现有
  `total_tokens_generated` 已按 `output_tokens.len()` 统计模型 token；
  重复计数器不能解释模型身份问题，否决。

## Acceptance criteria

- 7B 包补齐 provenance 后，`audit_provenance.py --expect-valid --check-files`
  通过，且 `tiny_llm_bench` 与 `paged-serving` 的 TPOT 差异能用同一
  模型、同一计时边界解释；或包保持 invalid 并删除所有公开引用。
- 若 `/readyz` 仍复现：加载中返回 503，加载完成后 200，回归测试覆盖
  慢加载、加载失败和请求拒绝三种路径。
- 若 TPOT 仍异常：给出“真快/假象”的书面结论，并有防再犯断言；
  没有结论前不引用该数字。

## Risks

- 重跑需要稳定 24GB+ GPU；当前只有 RTX 3060 Laptop 6GB，Node B 是
  租用临时节点，可能无法复现。
- 如果最终发现是模型身份错误，7B 包的 KV 公式观测也要降级；这会减少
  一个可讲的 7B 故事，但比保留错误数字更安全。
- FFI step 计时探针若默认开启会影响性能；必须默认关闭、只用于诊断。
