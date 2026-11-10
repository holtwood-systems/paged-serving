# Serving 对照包 — RTX 5070 Ti（FFI CUDA Graph OFF）

同模型、同后端、同负载矩阵的 `TLLM_FFI_CUDA_GRAPHS=0` 对照，矩阵为
closed c1/2/4/8 × work × 3 repeats（12 run）。主报告与结论见
`../2026-11-10-RTX5070Ti-graph-on/report.md`。

| 组合 | tok/s（on: → off） | TPOT p50 ms |
|---|---|---|
| c1 | 282.5 → 193.8 | 2.79 → 4.34 |
| c2 | 297.3 → 202.9 | 5.25 → 8.41 |
| c4 | 305.4 → 205.3 | 10.19 → 16.40 |
| c8 | 310.2 → 208.7 | 19.99 → 32.30 |

收敛：全部组合 converged。审计：`audit_provenance.py --expect-valid
--check-files` 通过；`validate_results.py` 12/12 通过。
