import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import audit_provenance  # noqa: E402


COMMIT = "a" * 40
SHA = "b" * 64

VALID = {
    "schema_version": 1,
    "status": "valid",
    "engine": {"name": "paged-serving", "commit": COMMIT},
    "backend": {"name": "tiny-llm", "commit": COMMIT},
    "model": {
        "path": "/data/models/qwen2.5-7b-instruct-q4_k_m.gguf",
        "sha256": SHA,
        "bytes": 4470000000,
        "architecture": "qwen2",
        "num_layers": 28,
        "hidden_dim": 3584,
        "num_kv_heads": 4,
        "head_dim": 128,
    },
    "tokenizer": {
        "kind": "huggingface",
        "path": "/data/models/Qwen2.5-7B-Instruct/tokenizer.json",
        "sha256": SHA,
    },
    "hardware": {
        "gpu": "NVIDIA GeForce RTX 4090",
        "driver": "550.107.02",
        "cuda": "12.8.61",
    },
    "command": [
        "paged-serving",
        "--serve",
        "--backend",
        "tiny-llm",
        "--model-path",
        "/data/models/qwen2.5-7b-instruct-q4_k_m.gguf",
        "--tokenizer",
        "/data/models/Qwen2.5-7B-Instruct/tokenizer.json",
    ],
    "server_log": "server.log",
    "load": {"ready_after_s": 300, "observed_gpu_mem_mib": 18432},
}


class AuditProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data=None, log="tinyllm_load model_path=/data/models/model.gguf\n"):
        data = copy.deepcopy(VALID if data is None else data)
        (self.root / "provenance.json").write_text(json.dumps(data), encoding="utf-8")
        if log is not None:
            (self.root / "server.log").write_text(log, encoding="utf-8")

    def codes(self, result):
        return {issue["code"] for issue in result["issues"]}

    def test_valid_package(self):
        self.write()
        result = audit_provenance.audit_package(self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["issues"], [])

    def test_missing_provenance_is_invalid(self):
        result = audit_provenance.audit_package(self.root)
        self.assertFalse(result["ok"])
        self.assertIn("missing_provenance", self.codes(result))

    def test_engine_ready_marker_is_accepted(self):
        self.write(log="Engine ready: backend=TinyLlm, model_path=/data/models/model.gguf\n")
        result = audit_provenance.audit_package(self.root)
        self.assertTrue(result["ok"], result)

    def test_missing_server_log_is_invalid(self):
        self.write(log=None)
        result = audit_provenance.audit_package(self.root)
        self.assertIn("missing_server_log", self.codes(result))

    def test_command_requires_tokenizer_for_huggingface(self):
        data = copy.deepcopy(VALID)
        data["command"] = ["paged-serving", "--model-path", data["model"]["path"]]
        self.write(data)
        result = audit_provenance.audit_package(self.root)
        self.assertIn("missing_tokenizer_flag", self.codes(result))

    def test_short_commit_is_invalid(self):
        data = copy.deepcopy(VALID)
        data["engine"]["commit"] = "abc"
        self.write(data)
        result = audit_provenance.audit_package(self.root)
        self.assertIn("invalid_commit", self.codes(result))

    def test_simple_tokenizer_requires_reason(self):
        data = copy.deepcopy(VALID)
        data["tokenizer"] = {"kind": "simple"}
        data["command"] = ["paged-serving", "--model-path", data["model"]["path"]]
        self.write(data)
        result = audit_provenance.audit_package(self.root)
        self.assertIn("missing_field", self.codes(result))

    def test_missing_tokenizer_does_not_crash(self):
        data = copy.deepcopy(VALID)
        del data["tokenizer"]
        self.write(data)
        result = audit_provenance.audit_package(self.root)
        self.assertIn("missing_field", self.codes(result))

    def test_expect_valid_rejects_invalid_status(self):
        data = copy.deepcopy(VALID)
        data["status"] = "invalid"
        self.write(data)
        result = audit_provenance.audit_package(self.root, expect_valid=True)
        self.assertIn("not_valid", self.codes(result))

    def test_check_files_detects_hash_mismatch(self):
        model = self.root / "model.gguf"
        tokenizer = self.root / "tokenizer.json"
        model.write_bytes(b"model")
        tokenizer.write_bytes(b"tokenizer")
        data = copy.deepcopy(VALID)
        data["model"].update(path=str(model), bytes=model.stat().st_size,
                             sha256="0" * 64)
        data["tokenizer"].update(path=str(tokenizer),
                                 sha256=hashlib.sha256(tokenizer.read_bytes()).hexdigest())
        self.write(data)
        result = audit_provenance.audit_package(self.root, check_files=True)
        self.assertIn("file_sha256_mismatch", self.codes(result))

    def test_check_files_accepts_matching_files(self):
        model = self.root / "model.gguf"
        tokenizer = self.root / "tokenizer.json"
        model.write_bytes(b"model")
        tokenizer.write_bytes(b"tokenizer")
        data = copy.deepcopy(VALID)
        data["model"].update(path=str(model), bytes=model.stat().st_size,
                             sha256=hashlib.sha256(model.read_bytes()).hexdigest())
        data["tokenizer"].update(path=str(tokenizer),
                                 sha256=hashlib.sha256(tokenizer.read_bytes()).hexdigest())
        self.write(data)
        result = audit_provenance.audit_package(self.root, check_files=True)
        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
