# serving readiness 竞态 + 7B TPOT 低于带宽下限（未解）

Status: proposed

## Problem

两个在 7B serving 实测中暴露、尚未定位/修复的问题（证据包
`tests/evidence/2026-11-06-7b-serving/`）：

1. **readiness 竞态**：`/readyz` 在 GGUF 权重仍在后台上传期间
   返回 200（7B 全量加载 ~5min，GPU 3.5→18GB 爬升窗口内请求
   已受理并产生基于部分权重的输出）。预热期输出不可信。
2. **7B TPOT ~2.8ms 低于物理下限**：W8A16 ~7.7GB 权重单步
   GEMV 读取在 4090 的理论下限 ≈7.6ms，实测 2.8–3.2ms。
   已排除：stub/CPU 回退（不存在）、输出造假（FFI 7B 对照
   测试通过、输出与 standalone 逐字一致）、预热窗口（复测
   稳定后依旧）。

## Proposal

- readiness：引擎暴露 `model_loaded` 状态，`/readyz` 在权重
  加载未完成时返回 503；加载期请求排队而非用部分权重生成。
- TPOT 异常先定量再定性：
  1. FFI 层加 step 计时探针（`TLLM_FFI_DEBUG` 扩展），确认
     `tinyllm_step` 单步真实耗时；
  2. 用 `paged_engine_tokens_generated_total` 增量交叉验证 wall；
  3. 若 step 真实 <7.6ms → 排查少算权重（dispatch 分片、
     is_prefill/position 语义使一步产出多 token 的误标）。

## Acceptance criteria

- 加载中请求返回 503 或排队，加载完成后输出与 standalone
  `tiny_llm_demo` 一致；回归测试覆盖。
- TPOT 归因有二选一的书面结论：真快 → paged decode 路径的
  权重读取节省被量化解释；假象 → 修正计时口径并加防再犯断言。

## Alternatives considered

- 维持现状只记文档：放弃。"预热期输出看似正常实则劣质"是
  静默错误，比显式失败更伤可信度；且 2.8ms 若属实则意味着
  未理解的计算路径，不能放着不管。
- 先修 TPOT 后修 readiness：否。readiness 是小改动高收益
  （正确性底线），先行；TPOT 作为调查项跟进。
