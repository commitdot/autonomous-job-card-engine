"""Tests for the Unsloth recipe: dataset formatting, manifest generation and dry-run flow."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_RECIPE_DIR = Path(__file__).resolve().parent.parent / "recipes" / "unsloth"
sys.path.insert(0, str(_RECIPE_DIR))

import dataset_formatter as dsf  # noqa: E402
import train_cartridge as tc  # noqa: E402

from src.models import AdapterCartridge  # noqa: E402

SAMPLE = _RECIPE_DIR / "sample_system_design_dataset.jsonl"


class TestDatasetFormatter(unittest.TestCase):
    def test_text_pairs_alpaca(self):
        recs = dsf.from_text_pairs([("Q?", "A.")], fmt="alpaca")
        self.assertEqual(recs, [{"instruction": "Q?", "input": "", "output": "A."}])

    def test_text_pairs_sharegpt_with_system(self):
        recs = dsf.from_text_pairs([("Q?", "A.")], fmt="sharegpt", system="Be brief")
        self.assertEqual(
            recs[0]["conversations"],
            [{"from": "system", "value": "Be brief"},
             {"from": "human", "value": "Q?"},
             {"from": "gpt", "value": "A."}],
        )

    def test_json_list_custom_keys(self):
        recs = dsf.from_json_list(
            [{"problem": "P", "solution": "S", "ctx": "C"}],
            fmt="alpaca", instruction_key="problem", output_key="solution", input_key="ctx",
        )
        self.assertEqual(recs[0], {"instruction": "P", "input": "C", "output": "S"})

    def test_json_list_missing_key_raises(self):
        with self.assertRaises(dsf.DatasetFormatError):
            dsf.from_json_list([{"problem": "P"}], output_key="solution", instruction_key="problem")

    def test_task_trace_with_steps(self):
        rec = dsf.from_task_trace({
            "task": "Fix bug", "context": "file a.py",
            "steps": ["read file", {"action": "patch", "observation": "ok"}],
            "result": "Done",
        })
        self.assertEqual(rec["instruction"], "Fix bug")
        self.assertEqual(rec["input"], "file a.py")
        self.assertIn("1. read file", rec["output"])
        self.assertIn("2. patch -> ok", rec["output"])
        self.assertTrue(rec["output"].endswith("Done"))

    def test_task_trace_requires_result(self):
        with self.assertRaises(dsf.DatasetFormatError):
            dsf.from_task_trace({"task": "x"})

    def test_empty_values_rejected(self):
        with self.assertRaises(dsf.DatasetFormatError):
            dsf.make_alpaca("", "out")
        with self.assertRaises(dsf.DatasetFormatError):
            dsf.make_sharegpt([("human", "only question")])
        with self.assertRaises(dsf.DatasetFormatError):
            dsf.make_sharegpt([("bot", "x"), ("gpt", "y")])

    def test_roundtrip_conversion(self):
        alp = dsf.make_alpaca("Do X", "Done", "ctx")
        sg = dsf.convert_record(alp, "sharegpt")
        self.assertEqual(dsf.detect_format(sg), "sharegpt")
        back = dsf.convert_record(sg, "alpaca")
        self.assertEqual(back["instruction"], "Do X\n\nctx")
        self.assertEqual(back["output"], "Done")

    def test_detect_format_unknown(self):
        with self.assertRaises(dsf.DatasetFormatError):
            dsf.detect_format({"foo": 1})

    def test_jsonl_io_and_convert_file(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d, "a.jsonl"), Path(d, "sub", "b.jsonl")
            self.assertEqual(dsf.write_jsonl([dsf.make_alpaca("i", "o")], src), 1)
            self.assertEqual(dsf.convert_file(src, dst, "sharegpt"), 1)
            self.assertEqual(dsf.detect_format(dsf.read_jsonl(dst)[0]), "sharegpt")

    def test_invalid_jsonl_reports_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "bad.jsonl")
            p.write_text('{"instruction": "a", "output": "b"}\nnot json\n', encoding="utf-8")
            with self.assertRaises(dsf.DatasetFormatError) as ctx:
                dsf.read_jsonl(p)
            self.assertIn(":2:", str(ctx.exception))

    def test_render_training_text(self):
        text = dsf.render_training_text(dsf.make_alpaca("I", "O"), eos_token="</s>")
        self.assertIn("### Instruction:\nI", text)
        self.assertTrue(text.endswith("### Response:\nO</s>"))


class TestSampleDataset(unittest.TestCase):
    def test_sample_dataset_valid_and_covers_topics(self):
        recs = tc.load_dataset_records(SAMPLE)
        self.assertGreaterEqual(len(recs), 8)
        blob = json.dumps(recs).lower()
        for topic in ("url shortener", "rate limiter", "notification", "key-value"):
            self.assertIn(topic, blob)
        for r in recs:
            self.assertEqual(dsf.detect_format(r), "alpaca")

    def test_empty_dataset_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "empty.jsonl")
            p.write_text("", encoding="utf-8")
            with self.assertRaises(dsf.DatasetFormatError):
                tc.load_dataset_records(p)

    def test_malformed_record_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "bad.jsonl")
            p.write_text('{"instruction": "a", "output": ""}\n', encoding="utf-8")
            with self.assertRaises(dsf.DatasetFormatError):
                tc.load_dataset_records(p)


class TestManifest(unittest.TestCase):
    def test_manifest_validates_against_cartridge_schema(self):
        digest = "a" * 64
        m = tc.build_manifest("my-lora", "1.2.3", "qwen2.5-coder-7b", "org/repo", sha256=digest, lora_rank=32)
        cart = AdapterCartridge.from_dict(m)
        self.assertEqual(cart.spec.type, "lora")
        self.assertEqual(cart.spec.lora_sha256, digest)
        self.assertEqual(cart.spec.lora_rank, 32)
        self.assertEqual(cart.spec.lora_filename, "adapter.safetensors")
        self.assertEqual(cart.spec.base_models, ["qwen2.5-coder-7b"])

    def test_manifest_without_sha_is_valid(self):
        AdapterCartridge.from_dict(tc.build_manifest("my-lora", "1.0.0", "llama-3.1-8b-instruct", "org/repo"))

    def test_unknown_base_model(self):
        with self.assertRaises(SystemExit):
            tc.build_manifest("x", "1.0.0", "gpt-9000", "org/repo")

    def test_write_manifest_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            m = tc.build_manifest("my-lora", "1.0.0", "llama-3.2-3b-instruct", "org/repo", sha256="b" * 64)
            path = tc.write_manifest(m, Path(d, "out", "cartridge.yaml"))
            self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8")), m)

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "adapter.safetensors")
            p.write_bytes(b"weights" * 20000)
            self.assertEqual(tc.sha256_file(p), hashlib.sha256(b"weights" * 20000).hexdigest())

    def test_all_base_models_resolve(self):
        for key in tc.BASE_MODELS:
            self.assertIn("unsloth/", tc.resolve_base_model(key)["hf_id"])


class TestDryRun(unittest.TestCase):
    def test_dry_run_writes_nothing_and_prints_plan(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "out")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = tc.main(["--dry-run", "--output-dir", str(out), "--dataset", str(SAMPLE),
                              "--base-model", "llama-3.1-8b-instruct"])
            self.assertEqual(rc, 0)
            self.assertFalse(out.exists())
            text = buf.getvalue()
            self.assertIn("unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit", text)
            self.assertIn("kind: AdapterCartridge", text)
            self.assertIn("[dry-run]", text)

    def test_dry_run_does_not_import_unsloth(self):
        with contextlib.redirect_stdout(io.StringIO()):
            tc.main(["--dry-run"])
        self.assertNotIn("unsloth", sys.modules)

    def test_train_without_unsloth_exits_cleanly(self):
        if "unsloth" in sys.modules or _has_module("unsloth"):
            self.skipTest("unsloth installed")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stdout(io.StringIO()):
                    tc.main(["--output-dir", d, "--dataset", str(SAMPLE)])
            self.assertIn("unsloth", str(ctx.exception))


def _has_module(name):
    import importlib.util
    return importlib.util.find_spec(name) is not None


if __name__ == "__main__":
    unittest.main()
