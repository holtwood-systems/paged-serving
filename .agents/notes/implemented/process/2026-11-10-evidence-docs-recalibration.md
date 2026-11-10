# Agent Note: 4090 证据包文档口径修正（双 commit、收敛表述、7B 状态）

Status: implemented

## Problem

`2026-11-05-RTX4090-exclusive-gpu/report.md` 与本仓 ROADMAP 存在三处
与原始证据不一致的口径，私有声明表（career-prep `evidence/claims.md`
「公开材料待校对」）已登记待修：

1. report.md 写被测代码为 `d7e2518`，但包内 `metadata.json` 与各 run
   `run_metadata.json` 记录 sweep 时 worktree HEAD 为 `186351e`。
2. report.md 称「各指标 `(max-min)/mean` 均 <4%」，而正式收敛门为
   10%（`validate_results.py`），且按 summary.json 重算，c2 TTFT p95
   极差 6.75%、c8 吞吐 4.12%，非门槛指标 tpot_p95 最大 12.36%。
3. ROADMAP 把 2026-11-06 7B canary 勾选为「真实实测验证」完成项，
   与该包 AUDIT.md 的 invalid/unverified 结论矛盾。

## Decision

- report.md 被测代码改为如实记录：sweep 时 HEAD 为 `186351e`；补充
  `186351e` 相对 `d7e2518` 仅新增共享包结果文件（`src/`、`tests/`、
  `Cargo.*`、`build.rs` 零 diff，`git diff --stat` 可复核），故与共享包
  「同一二进制」的声明在实质上成立——修正的是记录而非结论。
- 「均 <4%」改为：4 档并发全部通过 10% 收敛门（门槛三指标
  ttft_p95/tok/s/req/s），最大相对极差 6.75%（c2 TTFT p95）；非门槛
  指标 tpot_p95 最大极差 12.36%（c2）如实保留、不作收敛声明。
- ROADMAP 7B 条目改为「canary 采集并完成审计，整包 invalid/unverified，
  包内观测不作数」；审计目录与重跑条件保留，不重写历史包内容。
- BENCHMARKS.md 表内「全收敛」补「10% 收敛门」限定，与 ROADMAP 口径一致。

## Alternatives considered

- **在 report.md 只标「版本未核验」**——符合「不能查明就标记」的退路；
  但 `git diff --stat` 已给出可复核证据（两 commit 仅差结果文件），
  如实记录比模糊标注信息量大，否决。
- **把 <4% 保留为描述性数字**——部分指标确实 <4%，但「各指标均 <4%」
  是假命题且错引门槛值；按门槛口径改写并可复核，否决保留。
- **把 7B 条目从 ROADMAP 删除**——抹掉负结果痕迹违背本仓证据纪律；
  改为 invalid 口径保留条目与审计链接。

## Consequences

- 证据包正文与其 metadata/audit 自洽；「同一二进制」声明获得可复核的
  git 依据而非口头保证。
- ROADMAP 不再出现「已完成的 7B 实测」口径；invalid 结论在 README、
  BENCHMARKS、ROADMAP、AUDIT、claims.md 五处一致。
- 代价：tpot_p95 12.36% 这类非门槛大极差被显式写出，可能引来追问；
  这是刻意保留的诚实边界。

## Verification

- `python3 benchmarks/serving/validate_results.py --formal --json` 对
  独占包、共享包均 formal=true、0 errors/0 warnings；3060 正式矩阵包
  保持既有 warnings（非本次范围）。
- `audit_provenance.py --json` 对失效 7B 包仍返回 `ok: false`
  （missing_provenance），审计门未被本次文档修正放宽。
- `git diff --stat d7e2518 186351e -- src/ tests/ Cargo.toml Cargo.lock
  build.rs .github/` 为空，支撑「同一二进制」。
- `npm run verify-notes` 通过。
