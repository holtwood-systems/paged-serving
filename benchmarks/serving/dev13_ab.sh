#!/usr/bin/env bash
# DEV-13 端到端 A/B 驱动：4 个臂顺序执行（服务器换 env 重启）。
#   A eager-scalar : TLLM_FFI_CUDA_GRAPHS=0
#   B eager-batch  : TLLM_FFI_CUDA_GRAPHS=0 TLLM_BATCH_DECODE=1
#   C graph-scalar : 默认 graph（当前强基线）
#   D graph+batch  : 默认 graph + TLLM_BATCH_DECODE=1（候选最优：批量行
#                    eager、非批量行仍走 graph）
# 用法: dev13_ab.sh <MODEL> <TOKENIZER> <RESULTS_ROOT>
set -euo pipefail
MODEL="$1"; TOKENIZER="$2"; ROOT="$3"
cd "$(dirname "$0")"
# ROOT 一律绝对化：cd 之后传入的相对路径会相对脚本目录二次解析。
ROOT="$(realpath -m "$ROOT")"
mkdir -p "$ROOT"
SERVER=../../target/release/paged-serving
PORT=3111

wait_ready() { # $1=log
    for i in $(seq 1 300); do
        grep -q "Engine ready" "$1" 2>/dev/null && return 0
        sleep 1
    done
    echo "server not ready: $1" >&2; return 1
}

run_arm() { # $1=arm-name, $@=env pairs
    local arm="$1"; shift
    local dir="$ROOT/$arm" log="$ROOT/${arm}.server.log"
    mkdir -p "$dir"
    env "$@" PAGED_SERVING_TINY_LLM_MAX_SEQS=8 "$SERVER" --serve \
        --backend tiny-llm --model-path "$MODEL" --port $PORT \
        --tokenizer "$TOKENIZER" > "$log" 2>&1 &
    local pid=$!
    trap "kill $pid 2>/dev/null || true" RETURN
    wait_ready "$log"
    ./run_sweep.sh --base-url "http://127.0.0.1:$PORT" --engine paged-serving \
        --model paged-serving --model-path "$MODEL" \
        --tokenizer "$TOKENIZER" --backend-quant W8A16 --cuda-archs 120 \
        --modes closed --concurrencies "1 2 4 8" --datasets work \
        --results-dir "$dir" --allow-dirty
    cp "$log" "$dir/server.log"
    kill $pid 2>/dev/null || true; wait $pid 2>/dev/null || true
    trap - RETURN
    sleep 3
}

mkdir -p "$ROOT"
run_arm eager-scalar TLLM_FFI_CUDA_GRAPHS=0
run_arm eager-batch  TLLM_FFI_CUDA_GRAPHS=0 TLLM_BATCH_DECODE=1
run_arm graph-scalar
run_arm graph-batch  TLLM_BATCH_DECODE=1
echo "DEV-13 sweep complete: $ROOT"
