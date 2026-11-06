#!/usr/bin/env python3
"""Fail-closed provenance audit for serving result packages.

`validate_results.py` checks internal consistency of a result package. This
script checks whether the package identifies *what* was run: exact engine and
backend commits, model and tokenizer hashes, GGUF geometry, GPU/driver/CUDA,
the command, the server log, and the load protocol.

Historical schema-v1 packages are intentionally not retrofitted. New packages
must add a `provenance.json` sidecar before they can be cited as formal.
"""

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
STATUSES = {"valid", "invalid", "unverified"}
TOKENIZER_KINDS = {"huggingface", "simple"}
LOG_MARKERS = ("tinyllm_load", "engine ready", "load weights", "model loaded", "loaded model", "loading model")


class AuditError(ValueError):
    def __init__(self, code, location, message):
        super().__init__(message)
        self.issue = {"code": code, "location": str(location), "message": message}


def require(condition, code, location, message):
    if not condition:
        raise AuditError(code, location, message)


def field(obj, key, location):
    require(key in obj, "missing_field", f"{location}.{key}", "缺少必需字段")
    return obj[key]


def mapping(value, location):
    require(isinstance(value, dict), "invalid_type", location, "必须是 JSON 对象")
    return value


def text(value, location):
    require(isinstance(value, str) and bool(value.strip()), "invalid_type", location,
            "必须是非空字符串")
    return value


def integer(value, location, minimum=0):
    require(type(value) is int and minimum <= value <= 2**64 - 1, "invalid_integer", location,
            f"必须是 {minimum}..2^64-1 范围的整数")
    return value


def number(value, location):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            "invalid_number", location, "必须是有限非负数")
    return value


def sha256(value, location):
    text(value, location)
    require(SHA256_RE.fullmatch(value) is not None, "invalid_sha256", location,
            "必须是 64 位小写十六进制 SHA-256")
    return value


def commit(value, location):
    text(value, location)
    require(COMMIT_RE.fullmatch(value) is not None, "invalid_commit", location,
            "必须是 40 位小写十六进制 commit")
    return value


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise AuditError("unreadable_file", path, str(error)) from error


def reject_constant(value):
    raise ValueError(f"JSON 禁止非有限常量 {value}")


def load_json(path):
    try:
        content = read_text(path)
    except AuditError:
        raise
    try:
        return json.loads(content, parse_constant=reject_constant)
    except ValueError as error:
        raise AuditError("invalid_json", path, str(error)) from error


def file_sha256(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise AuditError("unreadable_file", path, str(error)) from error
    return digest.hexdigest()


def flag_value(command, flag):
    for index, item in enumerate(command[:-1]):
        if item == flag:
            return command[index + 1]
    return None


def audit_package(root, expect_valid=False, check_files=False):
    """Return a machine-readable audit result. Never mutates the package."""
    root = Path(root)
    provenance_path = root / "provenance.json"
    issues = []
    warnings = []
    status = None

    def check(callback):
        try:
            callback()
        except AuditError as error:
            issues.append(error.issue)

    if not provenance_path.is_file():
        issues.append({
            "code": "missing_provenance",
            "location": str(provenance_path),
            "message": "缺少 provenance.json；该包不能作为正式结果",
        })
        return {
            "schema_version": 1,
            "package": str(root),
            "ok": False,
            "status": None,
            "issues": issues,
            "warnings": warnings,
        }

    try:
        data = load_json(provenance_path)
    except AuditError as error:
        return {
            "schema_version": 1,
            "package": str(root),
            "ok": False,
            "status": None,
            "issues": [error.issue],
            "warnings": warnings,
        }

    def check_status():
        nonlocal status
        status = text(field(data, "status", provenance_path), f"{provenance_path}.status")
        require(status in STATUSES, "invalid_status", f"{provenance_path}.status",
                f"status 必须是 {sorted(STATUSES)} 之一")
        if expect_valid:
            require(status == "valid", "not_valid", f"{provenance_path}.status",
                    "该包不是 valid，不能作为正式结果发布")

    def check_schema_value():
        require(field(data, "schema_version", provenance_path) == 1, "unsupported_schema",
                provenance_path, "当前只支持 schema_version=1")

    def check_repository(section):
        block = mapping(field(data, section, provenance_path), f"{provenance_path}.{section}")
        text(field(block, "name", provenance_path), f"{provenance_path}.{section}.name")
        commit(field(block, "commit", provenance_path), f"{provenance_path}.{section}.commit")

    def check_model():
        model = mapping(field(data, "model", provenance_path), f"{provenance_path}.model")
        text(field(model, "path", provenance_path), f"{provenance_path}.model.path")
        sha256(field(model, "sha256", provenance_path), f"{provenance_path}.model.sha256")
        integer(field(model, "bytes", provenance_path), f"{provenance_path}.model.bytes", 1)
        text(field(model, "architecture", provenance_path),
             f"{provenance_path}.model.architecture")
        for key in ("num_layers", "hidden_dim", "num_kv_heads", "head_dim"):
            integer(field(model, key, provenance_path), f"{provenance_path}.model.{key}", 1)

    def check_tokenizer():
        tokenizer = mapping(field(data, "tokenizer", provenance_path),
                            f"{provenance_path}.tokenizer")
        kind = text(field(tokenizer, "kind", provenance_path),
                    f"{provenance_path}.tokenizer.kind")
        require(kind in TOKENIZER_KINDS, "invalid_tokenizer_kind",
                f"{provenance_path}.tokenizer.kind", f"必须是 {sorted(TOKENIZER_KINDS)} 之一")
        if kind == "huggingface":
            text(field(tokenizer, "path", provenance_path), f"{provenance_path}.tokenizer.path")
            sha256(field(tokenizer, "sha256", provenance_path),
                   f"{provenance_path}.tokenizer.sha256")
        else:
            text(field(tokenizer, "reason", provenance_path),
                 f"{provenance_path}.tokenizer.reason")
            warnings.append({
                "code": "simple_tokenizer",
                "location": f"{provenance_path}.tokenizer",
                "message": "SimpleTokenizer 不是模型 tokenizer；必须保留 reason 并限制声明",
            })

    def check_hardware():
        hardware = mapping(field(data, "hardware", provenance_path), f"{provenance_path}.hardware")
        for key in ("gpu", "driver", "cuda"):
            text(field(hardware, key, provenance_path), f"{provenance_path}.hardware.{key}")

    def check_command():
        command = field(data, "command", provenance_path)
        require(isinstance(command, list) and len(command) >= 2
                and all(isinstance(item, str) and item for item in command),
                "invalid_command", f"{provenance_path}.command",
                "command 必须是非空字符串数组")
        require(flag_value(command, "--model-path") is not None, "missing_model_path_flag",
                f"{provenance_path}.command", "command 必须包含 --model-path <path>")
        tokenizer = data.get("tokenizer")
        kind = tokenizer.get("kind") if isinstance(tokenizer, dict) else None
        if kind == "huggingface":
            require(flag_value(command, "--tokenizer") is not None, "missing_tokenizer_flag",
                    f"{provenance_path}.command", "HF tokenizer 包必须包含 --tokenizer <path>")

    def check_server_log():
        name = text(field(data, "server_log", provenance_path), f"{provenance_path}.server_log")
        log_path = root / name
        require(log_path.is_file(), "missing_server_log", log_path, "server log 不存在")
        content = read_text(log_path)
        require(bool(content.strip()), "empty_server_log", log_path, "server log 为空")
        lowered = content.lower()
        require(any(marker in lowered for marker in LOG_MARKERS), "server_log_no_load_marker",
                log_path, f"server log 未包含模型加载标记 {LOG_MARKERS}")

    def check_load():
        load = mapping(field(data, "load", provenance_path), f"{provenance_path}.load")
        number(field(load, "ready_after_s", provenance_path), f"{provenance_path}.load.ready_after_s")
        integer(field(load, "observed_gpu_mem_mib", provenance_path),
                f"{provenance_path}.load.observed_gpu_mem_mib", 1)

    def check_files_exist():
        model = mapping(field(data, "model", provenance_path), f"{provenance_path}.model")
        model_path = Path(text(field(model, "path", provenance_path),
                               f"{provenance_path}.model.path"))
        require(file_sha256(model_path) == sha256(field(model, "sha256", provenance_path),
                                                  f"{provenance_path}.model.sha256"),
                "file_sha256_mismatch", model_path, "model sha256 与文件不一致")
        require(model_path.stat().st_size == integer(field(model, "bytes", provenance_path),
                                                     f"{provenance_path}.model.bytes", 1),
                "file_size_mismatch", model_path, "model bytes 与文件大小不一致")
        tokenizer = mapping(field(data, "tokenizer", provenance_path),
                            f"{provenance_path}.tokenizer")
        if tokenizer.get("kind") == "huggingface":
            tokenizer_path = Path(text(field(tokenizer, "path", provenance_path),
                                       f"{provenance_path}.tokenizer.path"))
            require(file_sha256(tokenizer_path) == sha256(
                field(tokenizer, "sha256", provenance_path),
                f"{provenance_path}.tokenizer.sha256"),
                "file_sha256_mismatch", tokenizer_path, "tokenizer sha256 与文件不一致")

    check(check_schema_value)
    check(check_status)
    check(lambda: check_repository("engine"))
    check(lambda: check_repository("backend"))
    check(check_model)
    check(check_tokenizer)
    check(check_hardware)
    check(check_command)
    check(check_server_log)
    check(check_load)
    if check_files and status == "valid":
        check(check_files_exist)

    return {
        "schema_version": 1,
        "package": str(root),
        "ok": not issues,
        "status": status,
        "issues": issues,
        "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="审计 serving 结果包的 provenance")
    parser.add_argument("package", type=Path, help="结果包目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--expect-valid", action="store_true", help="要求 status=valid")
    parser.add_argument("--check-files", action="store_true", help="重新计算 model/tokenizer hash")
    args = parser.parse_args(argv)

    result = audit_package(args.package, expect_valid=args.expect_valid,
                           check_files=args.check_files)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"package: {result['package']}")
        print(f"status: {result['status']}")
        print(f"ok: {result['ok']}")
        for issue in result["issues"]:
            print(f"ERROR {issue['code']} {issue['location']}: {issue['message']}")
        for warning in result["warnings"]:
            print(f"WARN  {warning['code']} {warning['location']}: {warning['message']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
