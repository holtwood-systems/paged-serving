# Agent Note: build.rs 未声明 CUDA 库搜索路径，-lcudart 依赖外部环境变量

Status: implemented

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

## Decision

build.rs 依次探测 `CUDA_HOME`/`CUDA_PATH`/`CUDA_ROOT`、`/usr/local/cuda/lib64`
符号链接、再到 `/usr/local/cuda-*/lib64` 按版本号取最高，目录内含
`libcudart.so` 即 `println!("cargo:rustc-link-search=native={}")`；
都找不到时打 warning 并保留现状（不致命——cudart 仍可能经由系统默认
路径解析）。运行时说明不变：服务进程本身通过 rpath/dlopen 也需要可见
libcudart，`LD_LIBRARY_PATH` 仍是部署侧要求，不在本方案写入 rpath。

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

## Consequences

- 干净 shell（无 `LIBRARY_PATH`/`RUSTFLAGS -L`）下
  `TINY_LLM_DIR=../tiny-llm/build cargo build --release --features tiny-llm`
  链接成功——新机器不再需要先踩一次链接失败再查文档。
- 未安装 CUDA 的机器上构建行为与现状一致（warning + 依赖系统默认路径），
  不引入新的硬失败。
- 运行时 `LD_LIBRARY_PATH`/ldconfig 要求不变；链接修复不解决服务进程的
  libcudart 动态解析。

## Verification

2026-11-05 RTX 4090 实测通过：`env -u LIBRARY_PATH -u LD_LIBRARY_PATH
-u RUSTFLAGS -u CUDA_*` 干净 shell 下 `cargo build --release
--features tiny-llm` 链接成功，build 输出确认发出
`rustc-link-search=native=/usr/local/cuda/lib64`（经 `/usr/local/cuda`
符号链接分支命中）。无 CUDA 机器行为与现状一致：`find_cuda_lib64`
返回 None → warning + 不新增 search path，依赖系统默认路径。
临时 workaround 记录保留在
`benchmarks/serving/results/2026-11-05-NVIDIA-GeForce-RTX-4090/report.md`；
运行时 `LD_LIBRARY_PATH` 需求不变（rpath 不在本方案范围）。
