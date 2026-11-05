# Agent Note: build.rs 未声明 CUDA 库搜索路径，-lcudart 依赖外部环境变量

Status: proposed

## Problem

`build.rs` 在设置 `TINY_LLM_DIR` 时发出 `cargo:rustc-link-lib=cudart`，但从未
发出对应的 `cargo:rustc-link-search=native=<cuda lib64>`。rust-lld 不读
ldconfig 缓存，在没有 `LIBRARY_PATH`/`RUSTFLAGS -L` 的机器上
`cargo build --features tiny-llm` 必然在最终链接阶段失败：

```
rust-lld: error: unable to find library -lcudart
```

2026-11-05 RTX 4090（CUDA 12.8，`/usr/local/cuda-12.8/lib64`）实测复现；
旧环境（3060 Laptop）能编过只是因为发行版把 cudart 放进了默认搜索路径。

## Proposal

build.rs 依次探测 `CUDA_HOME`/`CUDA_PATH`/`/usr/local/cuda/lib64`
（或 `cuda-*/lib64` glob），存在即
`println!("cargo:rustc-link-search=native={}")`；都找不到时打 warning 并
保留现状（不致命——cudart 仍可能经由系统默认路径解析）。同时对运行时补充
说明：服务进程本身通过 rpath/dlopen 也需要可见 libcudart，`LD_LIBRARY_PATH`
只是当前 workaround。

## Alternatives considered

- 什么都不改、只在 benchmark 文档记 workaround（现状）：每个新机器第一次构建
  都踩一次，文档不会主动出现在编译报错路径上。
- 在 build.rs 里写死 `/usr/local/cuda/lib64`：不可移植，否决。
- 改用 `cuda-driver-sys`/`cudart-sys` crate：引入新依赖解决一条 search path，
  收益不匹配。

## Risks

- glob 兜底 `/usr/local/cuda-*/lib64` 在多版本共存机器上可能选中非预期版本；
  应优先 `CUDA_HOME`/`CUDA_PATH` 显式环境变量。
- 链接能过不代表运行时能找到 `libcudart.so`；rpath 不写入本方案范围，
  部署文档仍需记录 `LD_LIBRARY_PATH` 或系统 ldconfig 要求。

## Acceptance criteria

- 干净 shell（无 `LIBRARY_PATH`/`RUSTFLAGS -L`）下
  `TINY_LLM_DIR=../tiny-llm/build cargo build --release --features tiny-llm`
  链接成功。
- 未安装 CUDA 的机器上构建行为与现状一致（warning + 依赖系统默认路径），
  不引入新的硬失败。

## Verification

复现命令：不带 `LIBRARY_PATH` 的干净 shell 执行
`TINY_LLM_DIR=../tiny-llm/build cargo build --release --features tiny-llm`；
修复后同样命令应直接链接成功。临时 workaround 已写入
`benchmarks/serving/results/2026-11-05-NVIDIA-GeForce-RTX-4090/report.md`
复现段。
