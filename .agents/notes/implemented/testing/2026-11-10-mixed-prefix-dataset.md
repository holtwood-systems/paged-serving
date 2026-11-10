# Agent Note: serving 评测新增 mixed_prefix 合成负载

Status: implemented

## Problem

DEV-02 要求 closed/Poisson 矩阵外补「有重复前缀、长短混合」的有界
工作负载；既有 short/work/long 三档均为独立随机 prompt，无共享
前缀结构，覆盖不到前缀复用与长度混合变量。

## Decision

`gen_synth.py` 新增 `mixed_prefix.jsonl`（200 条）：每 8 条共享
96-160 token 级公共前缀，后缀为 32-256 token 级随机文本；同一
`--seed` 下三档旧文件逐字节不变（新数据集的 rng 消费追加在旧循环
之后，md5 校验确认）。数据集文件本身随本提交入库，保证 provenance
可复现。

## Alternatives

- 运行期脚本现场拼接：不可复现、provenance 缺一环。
- 修改 work.jsonl 注入前缀：污染既有基线数据集的跨次可比性。

## Consequences

run_sweep.sh 的 `--datasets` 可直接含 `mixed_prefix`；首次用于
2026-11-10 RTX 5070 Ti DEV-02 基线。
