#!/usr/bin/env python3
"""Unsloth QLoRA training recipe that produces an AJE adapter cartridge.

Trains a LoRA adapter on an Alpaca/ShareGPT JSONL dataset with Unsloth (4-bit
QLoRA), exports ``adapter.safetensors``, computes its SHA-256 and writes a
``cartridge.yaml`` that ``aje adapter`` can install.

Supported base models: Qwen-2.5-Coder (7B/14B) and LLaMA-3.x (3.1-8B, 3.2-3B).

Usage::

    # Validate dataset + show the plan and manifest (no GPU / unsloth needed)
    python recipes/unsloth/train_cartridge.py --dry-run

    # Real training (GPU + `pip install unsloth`)
    python recipes/unsloth/train_cartridge.py \\
        --base-model qwen2.5-coder-7b \\
        --dataset recipes/unsloth/sample_system_design_dataset.jsonl \\
        --name system-design-architect-lora --hf-repo-id your-org/system-design-architect-lora
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))

import dataset_formatter as dsf  # noqa: E402

CARTRIDGE_API_VERSION = "agent.autonomous.io/v1alpha1"
CARTRIDGE_KIND = "AdapterCartridge"
ADAPTER_FILENAME = "adapter.safetensors"
DEFAULT_DATASET = _HERE / "sample_system_design_dataset.jsonl"

# key -> (Unsloth 4-bit HF model id, name used in cartridge spec.base_models)
BASE_MODELS: Dict[str, Dict[str, str]] = {
    "qwen2.5-coder-7b": {
        "hf_id": "unsloth/Qwen2.5-Coder-7B-Instruct-bnb-4bit",
        "cartridge_name": "qwen2.5-coder-7b",
    },
    "qwen2.5-coder-14b": {
        "hf_id": "unsloth/Qwen2.5-Coder-14B-Instruct-bnb-4bit",
        "cartridge_name": "qwen2.5-coder-14b",
    },
    "llama-3.1-8b-instruct": {
        "hf_id": "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit",
        "cartridge_name": "llama-3.1-8b-instruct",
    },
    "llama-3.2-3b-instruct": {
        "hf_id": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
        "cartridge_name": "llama-3.2-3b-instruct",
    },
}

LORA_TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


# ---------------------------------------------------------------------------
# Pure helpers (no GPU dependencies; unit tested)
# ---------------------------------------------------------------------------

def sha256_file(path: Path, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_base_model(key: str) -> Dict[str, str]:
    try:
        return BASE_MODELS[key]
    except KeyError:
        raise SystemExit(f"Unknown base model {key!r}. Choose from: {', '.join(BASE_MODELS)}")


def load_dataset_records(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Load and validate a JSONL dataset (Alpaca or ShareGPT). Raises on malformed rows."""
    records = dsf.read_jsonl(path)
    if not records:
        raise dsf.DatasetFormatError(f"Dataset is empty: {path}")
    for i, rec in enumerate(records):
        try:
            fmt = dsf.detect_format(rec)
            if fmt == "alpaca":
                dsf.make_alpaca(rec["instruction"], rec["output"], rec.get("input", ""))
            else:
                dsf.sharegpt_to_alpaca(rec)
        except (dsf.DatasetFormatError, KeyError) as exc:
            raise dsf.DatasetFormatError(f"{path} record {i}: {exc}") from exc
    return records[:limit] if limit else records


def build_manifest(
    name: str,
    version: str,
    base_model_key: str,
    hf_repo_id: str,
    sha256: str = "",
    lora_rank: int = 16,
    author: str = "aje-community",
    domain: str = "system-design",
    description: str = "",
    tags: Optional[Sequence[str]] = None,
    license: str = "Apache-2.0",
) -> Dict[str, Any]:
    """Build a ``cartridge.yaml`` mapping compatible with ``src.models.AdapterCartridge``."""
    base = resolve_base_model(base_model_key)
    return {
        "apiVersion": CARTRIDGE_API_VERSION,
        "kind": CARTRIDGE_KIND,
        "metadata": {
            "name": name,
            "version": version,
            "author": author,
            "description": description or f"QLoRA adapter for {domain} fine-tuned with Unsloth on {base['cartridge_name']}.",
            "license": license,
            "domain": domain,
            "tags": list(tags) if tags else ["lora", "unsloth", "qlora", domain],
        },
        "spec": {
            "type": "lora",
            "base_models": [base["cartridge_name"]],
            "lora_repo_id": hf_repo_id,
            "lora_filename": ADAPTER_FILENAME,
            "lora_sha256": sha256,
            "lora_rank": lora_rank,
        },
    }


def write_manifest(manifest: Dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(manifest, fh, sort_keys=False, allow_unicode=True)
    return path


def build_plan(args: argparse.Namespace, n_records: int) -> Dict[str, Any]:
    base = resolve_base_model(args.base_model)
    return {
        "base_model": base["hf_id"],
        "dataset": str(args.dataset),
        "records": n_records,
        "output_dir": str(args.output_dir),
        "lora": {"r": args.lora_rank, "alpha": args.lora_alpha, "target_modules": LORA_TARGET_MODULES},
        "train": {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "grad_accum": args.grad_accum,
            "learning_rate": args.learning_rate,
            "max_seq_length": args.max_seq_length,
            "load_in_4bit": True,
        },
    }


# ---------------------------------------------------------------------------
# Training (requires GPU + unsloth)
# ---------------------------------------------------------------------------

def train(args: argparse.Namespace, records: List[Dict[str, Any]]) -> Path:
    """Run Unsloth QLoRA training and return the path of the exported adapter.safetensors."""
    try:
        from unsloth import FastLanguageModel  # type: ignore  # must be imported before trl/transformers
        from datasets import Dataset  # type: ignore
        from transformers import TrainingArguments  # type: ignore
        from trl import SFTTrainer  # type: ignore
        import torch  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            f"Missing training dependency ({exc.name}). Install with `pip install unsloth` "
            "on a CUDA machine (see recipes/unsloth/README.md), or use --dry-run."
        )

    base = resolve_base_model(args.base_model)
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base["hf_id"],
        max_seq_length=args.max_seq_length,
        dtype=None,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_rank,
        target_modules=LORA_TARGET_MODULES,
        lora_alpha=args.lora_alpha,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=args.seed,
    )

    eos = tokenizer.eos_token or ""
    train_ds = Dataset.from_list([{"text": dsf.render_training_text(r, eos)} for r in records])

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
        args=TrainingArguments(
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            num_train_epochs=args.epochs,
            learning_rate=args.learning_rate,
            warmup_steps=5,
            lr_scheduler_type="linear",
            optim="adamw_8bit",
            weight_decay=0.01,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=1,
            seed=args.seed,
            output_dir=str(args.output_dir / "checkpoints"),
            report_to="none",
        ),
    )
    trainer.train()

    # save_pretrained on a PEFT model writes adapter_model.safetensors + adapter_config.json
    adapter_dir = args.output_dir / "lora_adapter"
    model.save_pretrained(str(adapter_dir), safe_serialization=True)
    tokenizer.save_pretrained(str(adapter_dir))
    produced = adapter_dir / "adapter_model.safetensors"
    if not produced.is_file():
        raise SystemExit(f"Expected safetensors export not found: {produced}")
    final = args.output_dir / ADAPTER_FILENAME
    shutil.copyfile(produced, final)
    return final


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Train an AJE LoRA cartridge with Unsloth QLoRA")
    ap.add_argument("--base-model", default="qwen2.5-coder-7b", choices=sorted(BASE_MODELS))
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="Alpaca or ShareGPT JSONL")
    ap.add_argument("--output-dir", type=Path, default=_REPO_ROOT / ".jobs" / "unsloth-output")
    ap.add_argument("--name", default="system-design-architect-lora", help="Cartridge name")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--hf-repo-id", default="your-org/system-design-architect-lora",
                    help="Hugging Face repo where adapter.safetensors will be published")
    ap.add_argument("--domain", default="system-design")
    ap.add_argument("--author", default="aje-community")
    ap.add_argument("--lora-rank", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--learning-rate", type=float, default=2e-4)
    ap.add_argument("--max-seq-length", type=int, default=2048)
    ap.add_argument("--max-samples", type=int, default=None)
    ap.add_argument("--seed", type=int, default=3407)
    ap.add_argument("--dry-run", action="store_true",
                    help="Validate dataset, print plan and manifest preview; no training or file writes")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    records = load_dataset_records(args.dataset, args.max_samples)
    plan = build_plan(args, len(records))
    print(json.dumps(plan, indent=2))

    if args.dry_run:
        preview = build_manifest(args.name, args.version, args.base_model, args.hf_repo_id,
                                 sha256="", lora_rank=args.lora_rank, author=args.author, domain=args.domain)
        print("\n[dry-run] cartridge.yaml preview (lora_sha256 is filled in after training):")
        print(yaml.safe_dump(preview, sort_keys=False, allow_unicode=True))
        print("[dry-run] No training performed.")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    adapter = train(args, records)
    digest = sha256_file(adapter)
    manifest = build_manifest(args.name, args.version, args.base_model, args.hf_repo_id,
                              sha256=digest, lora_rank=args.lora_rank, author=args.author, domain=args.domain)
    manifest_path = write_manifest(manifest, args.output_dir / "cartridge.yaml")
    print(f"\nAdapter:  {adapter}\nSHA-256:  {digest}\nManifest: {manifest_path}")
    print(f"Next: upload {ADAPTER_FILENAME} to {args.hf_repo_id}, then `aje adapter pull {args.name} --weights`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
