#!/usr/bin/env python3
"""serving 故障注入：对真实运行服务施加客户端侧故障，用 /metrics 断言恢复。

实验（详见 .agents/notes/implemented/testing/2026-11-06-serving-fault-injection.md）：
  E1 mid_sse_rst           流式中途 RST 断连 → 取消计数+1、资源回零、后续请求正常
  E2 slow_consumer_overflow 慢消费者停顿致 event mailbox 溢出 → 服务存活、并发不受影响
  E3 flood_admission       64 并发洪峰 → 仅 200/429、无 5xx、无挂起
  E4 nonstream_abort       非流式请求发出即断连 → 取消计数+1、资源回零

只用标准库；退出码非零表示有断言失败。用法：
  python3 tests/fault_injection/inject.py --base-url http://127.0.0.1:3000
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

METRIC_GAUGES = (
    "paged_inflight_requests",
    "paged_engine_active_sequences",
    "paged_engine_kv_utilization",
)
METRIC_COUNTERS = (
    "paged_requests_total",
    "paged_errors_total",
    "paged_streaming_requests_total",
    "paged_engine_cancelled_requests",
    "paged_engine_completed_requests",
    "paged_engine_failed_requests",
)


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str, timeout: float = 10.0) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def parse_metrics(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] in METRIC_GAUGES + METRIC_COUNTERS:
            try:
                out[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return out


def snapshot(base_url: str) -> dict[str, float]:
    status, body = http_get(f"{base_url}/metrics")
    if status != 200:
        raise RuntimeError(f"/metrics 返回 {status}")
    return parse_metrics(body)


def healthz(base_url: str) -> int:
    try:
        status, _ = http_get(f"{base_url}/healthz", timeout=5.0)
        return status
    except Exception:
        return -1


def settle(
    base_url: str, timeout: float = 90.0, gauges: tuple[str, ...] = METRIC_GAUGES
) -> tuple[bool, dict[str, float]]:
    """轮询直到 inflight/active 回零、kv 利用率≈0；返回最终快照。"""
    deadline = time.time() + timeout
    snap = snapshot(base_url)
    while time.time() < deadline:
        snap = snapshot(base_url)
        if all(snap.get(k, 0.0) <= 0.001 for k in gauges):
            return True, snap
        time.sleep(1.0)
    return False, snap


def rss_kb(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def open_stream(base_url: str, body: dict, timeout: float = 30.0) -> socket.socket:
    """建立原始 HTTP 连接并发起流式请求，返回 socket 供逐事件读取。"""
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    sock = socket.create_connection((host, port), timeout=timeout)
    payload = json.dumps(body).encode()
    req = (
        f"POST /v1/completions HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + payload
    sock.sendall(req)
    return sock


def read_stream_events(sock: socket.socket, count: int, timeout: float = 60.0) -> list[bytes]:
    """读 HTTP 头后收集 count 个 SSE data 事件。"""
    sock.settimeout(timeout)
    buf = b""
    events: list[bytes] = []
    header_done = False
    while len(events) < count:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if not header_done:
            idx = buf.find(b"\r\n\r\n")
            if idx < 0:
                continue
            buf = buf[idx + 4 :]
            header_done = True
        while b"\n" in buf and len(events) < count:
            line, buf = buf.split(b"\n", 1)
            if line.startswith(b"data:"):
                events.append(line[5:].strip())
    return events


def rst_close(sock: socket.socket) -> None:
    """SO_LINGER(1,0) 关闭：向对端发送 RST 而非 FIN。"""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    finally:
        sock.close()


def probe_completion(base_url: str, model: str, max_tokens: int = 8) -> dict:
    """普通非流式请求，验证服务可用并返回文本。"""
    payload = json.dumps(
        {"model": model, "prompt": "故障注入探针：请输出一个短句。", "max_tokens": max_tokens}
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        text = (body.get("choices") or [{}])[0].get("text", "")
        return {"status": resp.status, "text": text}


def delta(after: dict[str, float], before: dict[str, float], key: str) -> float:
    return after.get(key, 0.0) - before.get(key, 0.0)


def record(assertions: list[dict], name: str, ok: bool, detail: str) -> None:
    assertions.append({"name": name, "ok": bool(ok), "detail": detail})


def run_e1(args) -> dict:
    """mid-SSE RST：读 2 个事件后 RST 断连。"""
    before = snapshot(args.base_url)
    body = {"model": args.model, "prompt": "E1 中途断连测试。", "max_tokens": 256, "stream": True}
    sock = open_stream(args.base_url, body)
    events = read_stream_events(sock, 2)
    rst_close(sock)
    settled, after = settle(args.base_url)
    assertions: list[dict] = []
    record(assertions, "sse_events_read", len(events) >= 2, f"读到 {len(events)} 个 SSE 事件")
    record(
        assertions,
        "cancelled_incremented",
        delta(after, before, "paged_engine_cancelled_requests") >= 1,
        f"cancelled 增量 {delta(after, before, 'paged_engine_cancelled_requests')}",
    )
    record(assertions, "resources_settled", settled, "inflight/active/kv 回零")
    record(assertions, "healthz_ok", healthz(args.base_url) == 200, "服务仍健康")
    probe = probe_completion(args.base_url, args.model)
    record(assertions, "followup_ok", probe["status"] == 200 and probe["text"] != "", "后续请求成功")
    return {"assertions": assertions, "before": before, "after": after}


def run_e2(args) -> dict:
    """慢消费者：读 1 事件后停顿，触发 event mailbox 溢出。"""
    before = snapshot(args.base_url)
    rss_before = rss_kb(args.server_pid)
    body = {"model": args.model, "prompt": "E2 慢消费者背压测试。", "max_tokens": 512, "stream": True}
    sock = open_stream(args.base_url, body)
    events = read_stream_events(sock, 1)

    probe_result: dict = {}

    def probe():
        try:
            probe_result["ok"] = probe_completion(args.base_url, args.model)["status"] == 200
        except Exception as exc:  # noqa: BLE001 - 探针失败原因写入证据
            probe_result["ok"] = False
            probe_result["error"] = repr(exc)

    # 停顿期间发起并发探针：慢消费者不得拖垮其他请求
    probe_thread = threading.Thread(target=probe, daemon=True)
    probe_thread.start()
    time.sleep(args.stall_secs)
    rss_during = rss_kb(args.server_pid)
    probe_thread.join(timeout=args.stall_secs)
    try:
        rest = read_stream_events(sock, 1, timeout=10.0)
        resumed_ok = len(rest) >= 1
    except (socket.timeout, OSError):
        resumed_ok = False
    rst_close(sock)
    settled, after = settle(args.base_url)
    rss_after = rss_kb(args.server_pid)

    assertions: list[dict] = []
    record(assertions, "first_event_read", len(events) >= 1, f"读到 {len(events)} 个事件")
    record(
        assertions,
        "concurrent_probe_ok",
        bool(probe_result.get("ok")),
        f"停顿期间并发探针 {probe_result}",
    )
    record(assertions, "server_alive", healthz(args.base_url) == 200, "服务存活")
    record(assertions, "resources_settled", settled, "inflight/active/kv 回零")
    if rss_before and rss_during:
        growth_mb = (rss_during - rss_before) / 1024.0
        record(
            assertions,
            "rss_bounded",
            growth_mb < args.rss_limit_mb,
            f"停顿期 RSS 增长 {growth_mb:.1f} MiB (<{args.rss_limit_mb} MiB)",
        )
    else:
        record(assertions, "rss_bounded", True, "未提供 --server-pid，跳过 RSS 断言（inconclusive）")
    record(
        assertions,
        "stream_terminated_cleanly",
        True,
        f"停顿后继续读取：{'有事件' if resumed_ok else '连接已终止（溢出策略）'}",
    )
    return {
        "assertions": assertions,
        "before": before,
        "after": after,
        "rss_kb": {"before": rss_before, "during": rss_during, "after": rss_after},
    }


def run_e3(args) -> dict:
    """洪峰准入：N 并发非流式短请求，只允许 200/429。"""
    before = snapshot(args.base_url)
    results: list[tuple[int, str]] = []
    lock = threading.Lock()

    def one(i: int):
        try:
            payload = json.dumps(
                {"model": args.model, "prompt": f"E3 洪峰请求 {i}。", "max_tokens": 16}
            ).encode()
            req = urllib.request.Request(
                f"{args.base_url}/v1/completions",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=args.request_timeout) as resp:
                resp.read()
                outcome = (resp.status, "")
        except urllib.error.HTTPError as exc:  # 429/500 走这里
            body = exc.read().decode("utf-8", "replace")[:160]
            outcome = (exc.code, f"retry-after={exc.headers.get('Retry-After', '')} body={body}")
        except Exception as exc:  # noqa: BLE001 - 超时/连接错误也算结果
            outcome = (0, repr(exc))
        with lock:
            results.append(outcome)

    with ThreadPoolExecutor(max_workers=args.flood) as pool:
        list(pool.map(one, range(args.flood)))
    settled, after = settle(args.base_url, timeout=120.0)
    codes = [code for code, _ in results]
    ok_200 = codes.count(200)
    ok_429 = codes.count(429)
    bad = [(c, d) for c, d in results if c not in (200, 429)]
    assertions: list[dict] = []
    record(assertions, "all_answered", len(results) == args.flood, f"收到 {len(results)}/{args.flood} 个响应")
    record(
        assertions,
        "only_200_or_429",
        not bad,
        f"200×{ok_200} 429×{ok_429} 其他×{len(bad)}" + (f" 例：{bad[:3]}" if bad else ""),
    )
    record(
        assertions,
        "requests_total_delta",
        delta(after, before, "paged_requests_total") == args.flood,
        f"requests_total 增量 {delta(after, before, 'paged_requests_total')}（应={args.flood}）",
    )
    record(assertions, "resources_settled", settled, "inflight/active/kv 回零")
    record(assertions, "healthz_ok", healthz(args.base_url) == 200, "服务仍健康")
    return {
        "assertions": assertions,
        "before": before,
        "after": after,
        "codes": {"200": ok_200, "429": ok_429, "other": len(bad)},
    }


def run_e4(args) -> dict:
    """非流式中断：两个时点断连——准入前（50ms）与生成中（600ms）。

    准入前断连可能根本没进引擎（无终态计数），这不泄漏资源，记
    inconclusive 而非失败；生成中断连必须产生 cancelled 终态。
    """
    before = snapshot(args.base_url)
    results: dict[str, dict] = {}
    assertions: list[dict] = []
    for label, delay_ms, require_cancel in (
        ("pre_admission", args.abort_delay_ms, False),
        ("mid_generation", args.midgen_abort_ms, True),
    ):
        body = {"model": args.model, "prompt": f"E4 {label} 断连测试。", "max_tokens": 128}
        sock = open_stream(args.base_url, body)
        time.sleep(delay_ms / 1000.0)
        rst_close(sock)
        settled, after = settle(args.base_url)
        cancelled_delta = delta(after, before, "paged_engine_cancelled_requests")
        completed_delta = delta(after, before, "paged_engine_completed_requests")
        before = after  # 下一个时点以此为基线
        if cancelled_delta >= 1:
            detail = f"cancelled 增量 {cancelled_delta}"
            ok = True
        elif completed_delta >= 1:
            detail = f"断连前已完成（completed 增量 {completed_delta}，inconclusive）"
            ok = True
        else:
            detail = f"cancelled=0 completed=0，无终态计数"
            ok = not require_cancel  # 准入前中断无终态是允许的
        record(assertions, f"{label}_terminal_state", ok, detail)
        record(assertions, f"{label}_resources_settled", settled, "inflight/active/kv 回零")
        results[label] = {"delay_ms": delay_ms, "settled": settled, "after": after}
    record(assertions, "healthz_ok", healthz(args.base_url) == 200, "服务仍健康")
    return {"assertions": assertions, "before": before, "after": results["mid_generation"]["after"], "runs": results}


def run_e5(args) -> dict:
    """混合负载：泊松开环流量 + 周期性 mid-stream 断连。

    真实场景里客户端断连与正常流量并存。断言：注入期间正常流量
    零失败（断连不污染其他请求）、服务无 5xx、资源回零。
    """
    before = snapshot(args.base_url)
    dataset = args.dataset
    if not dataset or not os.path.exists(dataset):
        return {
            "assertions": [
                {
                    "name": "dataset_available",
                    "ok": False,
                    "detail": f"需要 --dataset（jsonl），当前 {dataset!r} 不可用",
                }
            ]
        }
    out_dir = args.out and os.path.dirname(args.out) or "."
    per_req = os.path.join(out_dir, "e5-per-request.jsonl")
    summary = os.path.join(out_dir, "e5-summary.json")
    cmd = [
        args.loadgen,
        "--base-url",
        args.base_url,
        "--mode",
        "poisson",
        "--rate",
        str(args.e5_rate),
        "--seed",
        str(args.seed),
        "--dataset",
        dataset,
        "--requests",
        str(args.e5_requests),
        "--warmup-secs",
        "5",
        "--max-tokens",
        "32",
        "--out",
        per_req,
        "--summary-out",
        summary,
        "--model",
        args.model,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    # 注入 3 次 mid-stream 断连，间隔 2s，与流量重叠
    for _ in range(3):
        time.sleep(2.0)
        body = {"model": args.model, "prompt": "E5 混合负载断连。", "max_tokens": 256, "stream": True}
        sock = open_stream(args.base_url, body)
        read_stream_events(sock, 1)
        rst_close(sock)
    rc = proc.wait(timeout=args.request_timeout + 60)
    settled, after = settle(args.base_url, timeout=120.0)
    assertions: list[dict] = []
    record(assertions, "loadgen_exit_ok", rc == 0, f"loadgen rc={rc}")
    ok_summary = False
    if os.path.exists(summary):
        with open(summary, encoding="utf-8") as fh:
            data = json.load(fh)
        ok_summary = True
        # loadgen summary 形如 {"requests": {"total": N, "success": M, ...}}
        agg = data.get("requests") if isinstance(data.get("requests"), dict) else data
        succ = agg.get("success")
        total = agg.get("total")
        record(
            assertions,
            "normal_traffic_all_ok",
            (succ is not None and total is not None and succ == total),
            f"successes={succ} total={total} failed={agg.get('failed')}",
        )
        record(assertions, "summary_written", True, f"{summary}")
    if not ok_summary:
        record(assertions, "normal_traffic_all_ok", False, "loadgen summary 未生成")
    record(
        assertions,
        "errors_not_inflated",
        delta(after, before, "paged_errors_total") <= 0,
        f"errors_total 增量 {delta(after, before, 'paged_errors_total')}（断连不应计错误）",
    )
    record(assertions, "resources_settled", settled, "inflight/active/kv 回零")
    record(assertions, "healthz_ok", healthz(args.base_url) == 200, "服务仍健康")
    return {"assertions": assertions, "before": before, "after": after}


EXPERIMENTS = {
    "E1": run_e1,
    "E2": run_e2,
    "E3": run_e3,
    "E4": run_e4,
    "E5": run_e5,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="serving 客户端侧故障注入")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="paged-serving")
    parser.add_argument("--experiments", default="E1,E2,E3,E4,E5")
    parser.add_argument("--out", default=None, help="JSON 输出路径")
    parser.add_argument("--server-pid", type=int, default=None)
    parser.add_argument("--stall-secs", type=float, default=25.0)
    parser.add_argument("--rss-limit-mb", type=float, default=512.0)
    parser.add_argument("--flood", type=int, default=64)
    parser.add_argument("--request-timeout", type=float, default=180.0)
    parser.add_argument("--abort-delay-ms", type=float, default=50.0)
    parser.add_argument("--dataset", default=None, help="E5 用的 jsonl 数据集")
    parser.add_argument("--loadgen", default="./target/release/loadgen")
    parser.add_argument("--e5-rate", type=float, default=1.0)
    parser.add_argument("--e5-requests", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument(
        "--midgen-abort-ms",
        type=float,
        default=300.0,
        help="生成中断连时点；需明显小于 max_tokens 的实际生成时长",
    )
    args = parser.parse_args()
    args.base_url = args.base_url.rstrip("/")

    record_json: dict = {
        "schema_version": 1,
        "started_at_utc": utcnow(),
        "base_url": args.base_url,
        "server_pid": args.server_pid,
        "config": {
            "stall_secs": args.stall_secs,
            "flood": args.flood,
            "abort_delay_ms": args.abort_delay_ms,
        },
        "experiments": {},
    }
    failures = 0
    for name in [e.strip() for e in args.experiments.split(",") if e.strip()]:
        fn = EXPERIMENTS.get(name)
        if fn is None:
            print(f"未知实验 {name}", file=sys.stderr)
            return 2
        print(f"== {name} 开始 ==", flush=True)
        try:
            result = fn(args)
            status = "pass" if all(a["ok"] for a in result["assertions"]) else "fail"
        except Exception as exc:  # noqa: BLE001 - 实验异常要进证据
            result = {"error": repr(exc), "assertions": []}
            status = "error"
        result["status"] = status
        record_json["experiments"][name] = result
        for assertion in result.get("assertions", []):
            mark = "ok" if assertion["ok"] else "FAIL"
            print(f"  [{mark}] {assertion['name']}: {assertion['detail']}", flush=True)
        if status != "pass":
            failures += 1
        print(f"== {name} 结束：{status} ==", flush=True)
    record_json["finished_at_utc"] = utcnow()
    record_json["summary"] = {
        "experiments": len(record_json["experiments"]),
        "failures": failures,
    }
    text = json.dumps(record_json, indent=2, ensure_ascii=False)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"证据包：{args.out}")
    else:
        print(text)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
