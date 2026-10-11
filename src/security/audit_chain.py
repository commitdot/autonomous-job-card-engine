"""Tamper-Evident SHA-256 Append-Only Audit Chain
AJE v4.0 Security Matrix Component

Provides a cryptographically linked ledger of all engine state transitions,
attestation checks, quarantine events, sandbox executions, and closed-loop syncs.
Guarantees audit traceability for SOC2 / ISO27001 / FedRAMP compliance.
"""

import os
import json
import time
import hashlib
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path


GENESIS_PREV_HASH = "0" * 64


@dataclass
class AuditBlock:
    """An immutable cryptographic block in the audit ledger."""
    index: int
    timestamp: float
    event_type: str
    actor: str
    payload: Dict[str, Any]
    prev_hash: str
    block_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditBlock":
        return cls(
            index=data["index"],
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            actor=data["actor"],
            payload=data.get("payload", {}),
            prev_hash=data["prev_hash"],
            block_hash=data["block_hash"],
        )


class AuditChain:
    """
    Append-only SHA-256 Merkle audit chain with disk persistence.
    """

    def __init__(self, workspace_root: str = ".", ledger_filename: str = ".jobs/audit_ledger.jsonl"):
        self.workspace_root = Path(workspace_root).resolve()
        self.ledger_path = self.workspace_root / ledger_filename
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_genesis()

    def _calculate_block_hash(
        self,
        index: int,
        timestamp: float,
        event_type: str,
        actor: str,
        payload: Dict[str, Any],
        prev_hash: str
    ) -> str:
        """Calculates deterministic SHA-256 hash for block fields."""
        payload_canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        raw_string = f"{index}|{timestamp:.6f}|{event_type}|{actor}|{payload_canonical}|{prev_hash}"
        return hashlib.sha256(raw_string.encode("utf-8")).hexdigest()

    def _ensure_genesis(self):
        """Initializes the ledger with a genesis block if it does not exist."""
        if not self.ledger_path.exists() or self.ledger_path.stat().st_size == 0:
            genesis_ts = time.time()
            genesis_payload = {"system": "AJE v4.0", "status": "Audit Chain Initialized", "version": "4.0.0"}
            genesis_hash = self._calculate_block_hash(
                index=0,
                timestamp=genesis_ts,
                event_type="GENESIS",
                actor="System-Bootstrap",
                payload=genesis_payload,
                prev_hash=GENESIS_PREV_HASH,
            )
            genesis_block = AuditBlock(
                index=0,
                timestamp=genesis_ts,
                event_type="GENESIS",
                actor="System-Bootstrap",
                payload=genesis_payload,
                prev_hash=GENESIS_PREV_HASH,
                block_hash=genesis_hash,
            )
            self.ledger_path.write_text(json.dumps(genesis_block.to_dict()) + "\n", encoding="utf-8")

    def get_latest_block(self) -> Optional[AuditBlock]:
        """Reads the most recent block in the ledger."""
        if not self.ledger_path.exists():
            return None

        lines = self.ledger_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return None

        last_line = lines[-1].strip()
        if not last_line:
            return None

        try:
            return AuditBlock.from_dict(json.loads(last_line))
        except Exception:
            return None

    def append_event(self, event_type: str, actor: str, payload: Dict[str, Any]) -> AuditBlock:
        """
        Appends a new event block cryptographically linked to the previous block.
        """
        latest = self.get_latest_block()
        if latest is None:
            self._ensure_genesis()
            latest = self.get_latest_block()

        index = (latest.index + 1) if latest else 0
        prev_hash = latest.block_hash if latest else GENESIS_PREV_HASH
        ts = time.time()

        block_hash = self._calculate_block_hash(
            index=index,
            timestamp=ts,
            event_type=event_type,
            actor=actor,
            payload=payload,
            prev_hash=prev_hash,
        )

        block = AuditBlock(
            index=index,
            timestamp=ts,
            event_type=event_type,
            actor=actor,
            payload=payload,
            prev_hash=prev_hash,
            block_hash=block_hash,
        )

        # Append to ledger file
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(block.to_dict()) + "\n")

        return block

    def verify_integrity(self) -> Tuple[bool, Optional[str]]:
        """
        Verifies the full integrity of the audit chain from genesis to tip.
        Returns (is_valid: bool, error_message: Optional[str]).
        """
        if not self.ledger_path.exists():
            return False, "Ledger file does not exist."

        lines = self.ledger_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return False, "Ledger file is empty."

        expected_prev_hash = GENESIS_PREV_HASH
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                block = AuditBlock.from_dict(data)
            except Exception as e:
                return False, f"Malformed block at line {i + 1}: {str(e)}"

            if block.index != i:
                return False, f"Block index discontinuity at line {i + 1}: expected {i}, got {block.index}"

            if block.prev_hash != expected_prev_hash:
                return False, f"Broken cryptographic link at block {i}: expected prev_hash '{expected_prev_hash}', got '{block.prev_hash}'"

            # Recompute block hash
            calc_hash = self._calculate_block_hash(
                index=block.index,
                timestamp=block.timestamp,
                event_type=block.event_type,
                actor=block.actor,
                payload=block.payload,
                prev_hash=block.prev_hash,
            )

            if block.block_hash != calc_hash:
                return False, f"Tampered block detected at index {i}: stored hash '{block.block_hash}' != calculated '{calc_hash}'"

            expected_prev_hash = block.block_hash

        return True, None

    def get_all_blocks(self) -> List[AuditBlock]:
        """Returns all blocks in the ledger."""
        if not self.ledger_path.exists():
            return []

        blocks = []
        for line in self.ledger_path.read_text(encoding="utf-8").strip().splitlines():
            line = line.strip()
            if line:
                try:
                    blocks.append(AuditBlock.from_dict(json.loads(line)))
                except Exception:
                    continue
        return blocks
