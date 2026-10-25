"""Dataset formatting utilities for Unsloth fine-tuning of AJE adapter cartridges.

Converts task traces, JSON lists, and (prompt, response) text pairs into the two
JSONL layouts Unsloth understands:

* **Alpaca**   -> ``{"instruction": ..., "input": ..., "output": ...}``
* **ShareGPT** -> ``{"conversations": [{"from": "human", "value": ...}, {"from": "gpt", "value": ...}]}``

Standard library only, so it can be imported without torch/unsloth installed.

CLI::

    python recipes/unsloth/dataset_formatter.py in.jsonl out.jsonl --to sharegpt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

FORMATS = ("alpaca", "sharegpt")

_ROLE_TO_SHAREGPT = {
    "system": "system",
    "user": "human",
    "human": "human",
    "assistant": "gpt",
    "gpt": "gpt",
}

ALPACA_PROMPT = (
    "Below is an instruction that describes a task, paired with an input that provides "
    "further context. Write a response that appropriately completes the request.\n\n"
    "### Instruction:\n{instruction}\n\n### Input:\n{input}\n\n### Response:\n{output}"
)


class DatasetFormatError(ValueError):
    """Raised when a record cannot be converted to the requested format."""


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def make_alpaca(instruction: str, output: str, input: str = "") -> Dict[str, str]:
    """Build a single Alpaca record."""
    instruction, output = (instruction or "").strip(), (output or "").strip()
    if not instruction or not output:
        raise DatasetFormatError("Alpaca records require non-empty instruction and output")
    return {"instruction": instruction, "input": (input or "").strip(), "output": output}


def make_sharegpt(turns: Sequence[Tuple[str, str]], system: str = "") -> Dict[str, List[Dict[str, str]]]:
    """Build a ShareGPT record from ``(role, text)`` turns. Roles: system/user/human/assistant/gpt."""
    conversations: List[Dict[str, str]] = []
    if system.strip():
        conversations.append({"from": "system", "value": system.strip()})
    for role, text in turns:
        if role not in _ROLE_TO_SHAREGPT:
            raise DatasetFormatError(f"Unknown role: {role!r}")
        if not (text or "").strip():
            raise DatasetFormatError("ShareGPT turns must be non-empty")
        conversations.append({"from": _ROLE_TO_SHAREGPT[role], "value": text.strip()})
    roles = [c["from"] for c in conversations if c["from"] != "system"]
    if len(roles) < 2 or roles[0] != "human" or roles[-1] != "gpt":
        raise DatasetFormatError("ShareGPT conversation must start with a human turn and end with a gpt turn")
    return {"conversations": conversations}


def _build(fmt: str, instruction: str, output: str, input: str = "", system: str = "") -> Dict[str, Any]:
    if fmt == "alpaca":
        return make_alpaca(instruction, output, input)
    if fmt == "sharegpt":
        prompt = f"{instruction}\n\n{input}".strip() if input else instruction
        return make_sharegpt([("human", prompt), ("gpt", output)], system=system)
    raise DatasetFormatError(f"Unsupported format: {fmt!r} (expected one of {FORMATS})")


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------

def from_text_pairs(
    pairs: Iterable[Tuple[str, str]], fmt: str = "alpaca", system: str = ""
) -> List[Dict[str, Any]]:
    """Convert ``(prompt, response)`` tuples."""
    return [_build(fmt, prompt, response, system=system) for prompt, response in pairs]


def from_json_list(
    items: Iterable[Mapping[str, Any]],
    fmt: str = "alpaca",
    instruction_key: str = "instruction",
    output_key: str = "output",
    input_key: str = "input",
    system: str = "",
) -> List[Dict[str, Any]]:
    """Convert a list of dicts, with configurable key names (e.g. ``problem``/``solution``)."""
    records = []
    for i, item in enumerate(items):
        if instruction_key not in item or output_key not in item:
            raise DatasetFormatError(f"Item {i} missing {instruction_key!r} or {output_key!r}")
        records.append(_build(fmt, str(item[instruction_key]), str(item[output_key]),
                              str(item.get(input_key, "") or ""), system=system))
    return records


def from_task_trace(trace: Mapping[str, Any], fmt: str = "alpaca", system: str = "") -> Dict[str, Any]:
    """Convert one AJE task trace.

    Expected keys: ``task`` (or ``title``/``description``) and ``result`` (or ``output``).
    Optional: ``context`` (becomes the input) and ``steps`` (list of str or
    ``{"action":..., "observation":...}``), which are prepended to the output as a
    numbered reasoning trail.
    """
    task = trace.get("task") or trace.get("title") or trace.get("description")
    result = trace.get("result") or trace.get("output")
    if not task or not result:
        raise DatasetFormatError("Task trace requires a task description and a result")
    lines = []
    for n, step in enumerate(trace.get("steps") or [], 1):
        if isinstance(step, Mapping):
            text = str(step.get("action", "")).strip()
            obs = str(step.get("observation", "")).strip()
            text = f"{text} -> {obs}" if text and obs else (text or obs)
        else:
            text = str(step).strip()
        if text:
            lines.append(f"{n}. {text}")
    output = ("Steps:\n" + "\n".join(lines) + "\n\nResult:\n" if lines else "") + str(result)
    return _build(fmt, str(task), output, str(trace.get("context", "") or ""), system=system)


def from_task_traces(traces: Iterable[Mapping[str, Any]], fmt: str = "alpaca", system: str = "") -> List[Dict[str, Any]]:
    return [from_task_trace(t, fmt, system) for t in traces]


# ---------------------------------------------------------------------------
# Conversion between formats
# ---------------------------------------------------------------------------

def detect_format(record: Mapping[str, Any]) -> str:
    if "conversations" in record:
        return "sharegpt"
    if "instruction" in record and "output" in record:
        return "alpaca"
    raise DatasetFormatError(f"Unrecognised record layout: keys={sorted(record)}")


def alpaca_to_sharegpt(record: Mapping[str, Any], system: str = "") -> Dict[str, Any]:
    return _build("sharegpt", record["instruction"], record["output"], record.get("input", ""), system=system)


def sharegpt_to_alpaca(record: Mapping[str, Any]) -> Dict[str, str]:
    convs = record["conversations"]
    humans = [c["value"] for c in convs if c["from"] == "human"]
    gpts = [c["value"] for c in convs if c["from"] == "gpt"]
    if not humans or not gpts:
        raise DatasetFormatError("ShareGPT record needs at least one human and one gpt turn")
    return make_alpaca(humans[0], gpts[-1])


def convert_record(record: Mapping[str, Any], to: str) -> Dict[str, Any]:
    src = detect_format(record)
    if src == to:
        return dict(record)
    return alpaca_to_sharegpt(record) if to == "sharegpt" else sharegpt_to_alpaca(record)


# ---------------------------------------------------------------------------
# JSONL I/O and training-text rendering
# ---------------------------------------------------------------------------

def write_jsonl(records: Iterable[Mapping[str, Any]], path: Union[str, Path]) -> int:
    """Write records as UTF-8 JSONL. Returns the number of lines written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
    return count


def iter_jsonl(path: Union[str, Path]) -> Iterator[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetFormatError(f"{path}:{lineno}: invalid JSON ({exc})") from exc


def read_jsonl(path: Union[str, Path]) -> List[Dict[str, Any]]:
    return list(iter_jsonl(path))


def render_training_text(record: Mapping[str, Any], eos_token: str = "") -> str:
    """Render a record to a single training string (Alpaca prompt template)."""
    if detect_format(record) == "sharegpt":
        record = sharegpt_to_alpaca(record)
    return ALPACA_PROMPT.format(
        instruction=record["instruction"], input=record.get("input", ""), output=record["output"]
    ) + eos_token


def convert_file(src: Union[str, Path], dst: Union[str, Path], to: str) -> int:
    if to not in FORMATS:
        raise DatasetFormatError(f"Unsupported format: {to!r}")
    return write_jsonl((convert_record(r, to) for r in iter_jsonl(src)), dst)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Convert a JSONL dataset between Alpaca and ShareGPT formats")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--to", choices=FORMATS, required=True)
    args = ap.parse_args(argv)
    n = convert_file(args.src, args.dst, args.to)
    print(f"Wrote {n} {args.to} records to {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
