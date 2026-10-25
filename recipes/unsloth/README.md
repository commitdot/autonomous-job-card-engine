# Unsloth QLoRA Recipe: Train an AJE Adapter Cartridge

Fine-tune a LoRA adapter with [Unsloth](https://github.com/unslothai/unsloth) (4-bit QLoRA) and package it as an AJE `AdapterCartridge` (`type: lora`).

| File | Purpose |
|------|---------|
| `dataset_formatter.py` | Convert task traces, JSON lists and text pairs to Alpaca / ShareGPT JSONL |
| `sample_system_design_dataset.jsonl` | 8 Alpaca pairs: URL shortener, sliding-window rate limiter, notification service, replicated key-value store |
| `train_cartridge.py` | Train, export `adapter.safetensors`, compute SHA-256, write `cartridge.yaml` |

Supported base models (`--base-model`): `qwen2.5-coder-7b`, `qwen2.5-coder-14b`, `llama-3.1-8b-instruct`, `llama-3.2-3b-instruct`.

The sample dataset is a small seed set written in the style of freeCodeCamp system design guides. It is enough to verify the pipeline, not to produce a production-quality adapter. Use hundreds to thousands of curated examples for real training.

## 1. Prepare data

```python
import sys; sys.path.insert(0, "recipes/unsloth")
import dataset_formatter as dsf

# (prompt, response) pairs
recs = dsf.from_text_pairs([("Design a cache.", "Use cache-aside with a TTL ...")], fmt="alpaca")

# list of dicts with custom keys
recs = dsf.from_json_list(items, fmt="sharegpt", instruction_key="problem", output_key="solution")

# AJE task traces: {"task", "context", "steps", "result"}
recs = dsf.from_task_traces(traces, fmt="alpaca")

dsf.write_jsonl(recs, "my_dataset.jsonl")
```

Convert an existing file: `python recipes/unsloth/dataset_formatter.py in.jsonl out.jsonl --to sharegpt`

## 2. Dry run (no GPU, no unsloth)

```bash
python recipes/unsloth/train_cartridge.py --dry-run
```

Validates the dataset, prints the training plan and a `cartridge.yaml` preview, and writes nothing.

## 3. Train

```bash
pip install unsloth        # needs a CUDA GPU
python recipes/unsloth/train_cartridge.py \
  --base-model qwen2.5-coder-7b \
  --dataset recipes/unsloth/sample_system_design_dataset.jsonl \
  --name system-design-architect-lora \
  --hf-repo-id your-org/system-design-architect-lora
```

Outputs in `.jobs/unsloth-output/` (override with `--output-dir`):

- `adapter.safetensors` - LoRA weights
- `lora_adapter/` - PEFT config and tokenizer
- `cartridge.yaml` - manifest with `lora_repo_id`, `lora_filename`, `lora_sha256`, `lora_rank`

Useful flags: `--epochs 3`, `--lora-rank 16`, `--max-seq-length 2048`, `--batch-size 2`, `--grad-accum 4`, `--learning-rate 2e-4`, `--max-samples N`.

## 4. Publish and install

1. Upload `adapter.safetensors` to the Hugging Face repo named in `--hf-repo-id`.
2. Copy `cartridge.yaml` to `registry/cartridges/<domain>/<name>/cartridge.yaml`.
3. `aje adapter pull <name> --weights`. The download is verified against `lora_sha256`; a mismatch is rejected.

## Google Colab setup

1. Runtime > Change runtime type > **T4 GPU** (free tier works for 3B-8B models).
2. In a cell:

```bash
!git clone <your-fork-url> aje && cd aje
!pip install unsloth pyyaml
!python recipes/unsloth/train_cartridge.py --base-model llama-3.2-3b-instruct --epochs 3
```

3. Download `.jobs/unsloth-output/adapter.safetensors` and `cartridge.yaml` from the Files panel, or push to the Hub with `huggingface_hub`.

Rough VRAM guide (4-bit QLoRA, seq length 2048, batch size 2): 3B models fit comfortably in 16 GB (T4); 7-8B models typically fit in 16 GB with small batches; 14B models usually need 24 GB or more. Lower `--max-seq-length` or `--batch-size` if you hit out-of-memory errors.

## Cost breakdown (estimates)

Approximate figures; check current provider pricing. Time depends on dataset size, sequence length and GPU. For a seed-size dataset (under ~1,000 examples, 3 epochs) a 7B model typically trains in minutes to tens of minutes.

| Option | GPU | Approx. cost | Notes |
|--------|-----|--------------|-------|
| Google Colab free | T4 16 GB | $0 | Session limits and availability vary; suits 3B-8B |
| Colab Pro | T4/L4/A100 (subject to availability) | roughly $10/month | Longer sessions |
| Cloud GPU rental (RunPod, Lambda, Vast.ai) | A10/L4/RTX 4090 | roughly $0.30-$1.00/hour | Pay per use; 14B models |
| Local | Your own 16-24 GB GPU | electricity only | Windows needs WSL2 or a supported setup |

Typical total for a seed-sized run: $0 on free Colab, or under about $1 on rented hardware. Storage and hosting of the adapter on Hugging Face is free for public repos. Adapter size is tens to a few hundred MB at rank 16, far smaller than the base model.

## Notes

- Training uses the Alpaca prompt template for both formats (ShareGPT is mapped to first human turn + last gpt turn).
- Keep `lora_rank` in the manifest consistent with `--lora-rank`; the script does this automatically.
- Review generated adapters before use; the cartridge benchmarks field is intentionally left for you to fill in with measured results.
