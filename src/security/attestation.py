"""Cryptographic Task Attestation & Safe Quarantine Vault
AJE v4.0 Security Matrix Component

Provides HMAC-SHA256 cryptographic attestation for Job Cards (Mother, SquadLead, Child)
to prevent rogue card generation/spoofing, and manages an isolated Quarantine Vault
for compromised or unverified task payloads without silent deletion (SOC2/ISO27001 compliant).
"""

import os
import json
import hmac
import hashlib
import time
import uuid
import stat
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path


DEFAULT_ATTESTATION_KEY = os.getenv("AJE_SIGNING_KEY", "aje-enterprise-attestation-secret-key-v4")


@dataclass
class CardSignature:
    """Cryptographic signature metadata attached to a Job Card."""
    algorithm: str = "HMAC-SHA256"
    key_id: str = "mother-root"
    signature: str = ""
    timestamp: float = field(default_factory=time.time)
    signed_fields: List[str] = field(default_factory=lambda: ["id", "title", "squad", "target_files", "validation_criteria"])
    issuer: str = "AJE-Mother-Daemon"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CardSignature":
        if not data:
            return cls()
        return cls(
            algorithm=data.get("algorithm", "HMAC-SHA256"),
            key_id=data.get("key_id", "mother-root"),
            signature=data.get("signature", ""),
            timestamp=data.get("timestamp", time.time()),
            signed_fields=data.get("signed_fields", ["id", "title", "squad", "target_files", "validation_criteria"]),
            issuer=data.get("issuer", "AJE-Mother-Daemon"),
        )


@dataclass
class QuarantineRecord:
    """Metadata record for a quarantined job card."""
    quarantine_id: str
    card_id: str
    original_path: str
    quarantined_path: str
    meta_path: str
    reason: str
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    timestamp: str
    sha256_payload_hash: str
    details: Dict[str, Any] = field(default_factory=dict)
    remediation: str = "Verify task origin, sign with authorized key, or purge via 'aje quarantine purge'."

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuarantineRecord":
        return cls(
            quarantine_id=data.get("quarantine_id", ""),
            card_id=data.get("card_id", ""),
            original_path=data.get("original_path", ""),
            quarantined_path=data.get("quarantined_path", ""),
            meta_path=data.get("meta_path", ""),
            reason=data.get("reason", ""),
            severity=data.get("severity", "HIGH"),
            timestamp=data.get("timestamp", ""),
            sha256_payload_hash=data.get("sha256_payload_hash", ""),
            details=data.get("details", {}),
            remediation=data.get("remediation", ""),
        )


class CardAttestationEngine:
    """Cryptographic attestation engine for signing and verifying job cards."""

    def __init__(self, secret_key: Optional[str] = None, key_id: str = "mother-root"):
        self.secret_key = (secret_key or DEFAULT_ATTESTATION_KEY).encode("utf-8")
        self.key_id = key_id

    def _canonicalize_payload(self, card_data: Dict[str, Any], signed_fields: List[str]) -> bytes:
        """Create a deterministic byte representation of the card's signed fields."""
        extracted: Dict[str, Any] = {}
        for f in signed_fields:
            if f in card_data:
                extracted[f] = card_data[f]
            elif f == "title" and "name" in card_data:
                extracted[f] = card_data["name"]
            elif f == "validation_criteria" and "acceptance_criteria" in card_data:
                extracted[f] = card_data["acceptance_criteria"]
            elif f == "target_files" and "files" in card_data:
                extracted[f] = card_data["files"]

        # Deterministic JSON encoding (sorted keys, compact separators)
        canonical_json = json.dumps(extracted, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return canonical_json.encode("utf-8")

    def sign_card(
        self,
        card_data: Dict[str, Any],
        signed_fields: Optional[List[str]] = None,
        issuer: str = "AJE-Mother-Daemon"
    ) -> Dict[str, Any]:
        """Signs card data and attaches an attestation signature block."""
        if signed_fields is None:
            signed_fields = ["id", "title", "squad", "target_files", "validation_criteria", "command"]

        # Filter signed_fields to only those present or mapped
        available_fields = [f for f in signed_fields if f in card_data or (f == "title" and "name" in card_data)]
        if not available_fields:
            available_fields = list(card_data.keys())

        payload_bytes = self._canonicalize_payload(card_data, available_fields)
        signature_hex = hmac.new(self.secret_key, payload_bytes, hashlib.sha256).hexdigest()

        sig = CardSignature(
            algorithm="HMAC-SHA256",
            key_id=self.key_id,
            signature=signature_hex,
            timestamp=time.time(),
            signed_fields=available_fields,
            issuer=issuer,
        )

        card_data["signature"] = sig.to_dict()
        return card_data

    def verify_card(self, card_data: Dict[str, Any], require_signature: bool = True) -> Tuple[bool, str]:
        """
        Verifies the cryptographic signature on a card.
        Returns (is_valid: bool, reason: str).
        """
        sig_data = card_data.get("signature")
        if not sig_data:
            if require_signature:
                return False, "Missing cryptographic signature block (unsigned card)"
            return True, "Card is unsigned but signatures are not strictly required"

        try:
            sig = CardSignature.from_dict(sig_data)
        except Exception as e:
            return False, f"Malformed signature block: {str(e)}"

        if sig.algorithm != "HMAC-SHA256":
            return False, f"Unsupported signing algorithm: {sig.algorithm}"

        payload_bytes = self._canonicalize_payload(card_data, sig.signed_fields)
        expected_sig = hmac.new(self.secret_key, payload_bytes, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(sig.signature, expected_sig):
            return False, "Signature verification failed: payload hash mismatch (tampered or spoofed)"

        return True, f"Attestation valid (verified by key '{sig.key_id}' from issuer '{sig.issuer}')"


class QuarantineVault:
    """
    Quarantine Vault for isolating untrusted, forged, or dangerous cards.
    NEVER silently drops or deletes cards — seals them with metadata for forensics.
    """

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root).resolve()
        self.quarantine_dir = self.workspace_root / ".jobs" / "quarantine"
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def quarantine_card(
        self,
        card_data_or_path: Any,
        reason: str,
        severity: str = "HIGH",
        details: Optional[Dict[str, Any]] = None,
        source_file_path: Optional[str] = None
    ) -> QuarantineRecord:
        """
        Quarantines a card. If a source file path is provided or given, moves/copies
        the card to the quarantine vault in a disarmed state with companion .meta.json.
        """
        details = details or {}
        card_id = "unknown"
        raw_content = ""
        card_dict: Dict[str, Any] = {}

        if isinstance(card_data_or_path, (str, Path)) and os.path.exists(str(card_data_or_path)):
            src_path = Path(card_data_or_path)
            source_file_path = str(src_path)
            try:
                raw_content = src_path.read_text(encoding="utf-8")
                card_dict = json.loads(raw_content)
                card_id = card_dict.get("id", src_path.stem)
            except Exception:
                card_id = src_path.stem
        elif isinstance(card_data_or_path, dict):
            card_dict = card_data_or_path
            card_id = card_dict.get("id", f"card-{uuid.uuid4().hex[:8]}")
            raw_content = json.dumps(card_dict, indent=2)
        else:
            raw_content = str(card_data_or_path)
            card_id = f"card-{uuid.uuid4().hex[:8]}"

        # Calculate payload SHA-256
        payload_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        quarantine_id = f"Q-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        safe_card_id = card_id.replace("/", "_").replace("\\", "_")

        quarantined_filename = f"{safe_card_id}_{quarantine_id}.quarantined.json"
        meta_filename = f"{safe_card_id}_{quarantine_id}.meta.json"

        quarantined_file = self.quarantine_dir / quarantined_filename
        meta_file = self.quarantine_dir / meta_filename

        # Write quarantined card content
        quarantined_file.write_text(raw_content, encoding="utf-8")
        
        # Disarm quarantined file (make read-only for owner/group/others where supported)
        try:
            os.chmod(str(quarantined_file), stat.S_IREAD)
        except Exception:
            pass

        # If source file existed on disk, safely remove it from active queue now that it's sealed
        if source_file_path and os.path.exists(source_file_path) and os.path.abspath(source_file_path) != str(quarantined_file):
            try:
                os.remove(source_file_path)
            except Exception as e:
                details["source_removal_error"] = str(e)

        iso_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record = QuarantineRecord(
            quarantine_id=quarantine_id,
            card_id=card_id,
            original_path=source_file_path or "memory_payload",
            quarantined_path=str(quarantined_file.relative_to(self.workspace_root)).replace("\\", "/"),
            meta_path=str(meta_file.relative_to(self.workspace_root)).replace("\\", "/"),
            reason=reason,
            severity=severity.upper(),
            timestamp=iso_time,
            sha256_payload_hash=payload_hash,
            details=details,
        )

        meta_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        return record

    def list_quarantined_cards(self) -> List[QuarantineRecord]:
        """Lists all quarantined records currently in the vault."""
        records: List[QuarantineRecord] = []
        if not self.quarantine_dir.exists():
            return records

        for meta_file in sorted(self.quarantine_dir.glob("*.meta.json")):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                records.append(QuarantineRecord.from_dict(data))
            except Exception:
                continue
        return records

    def inspect_quarantined_card(self, quarantine_id_or_card_id: str) -> Optional[Tuple[Dict[str, Any], QuarantineRecord]]:
        """Retrieves the card payload and metadata for inspection."""
        records = self.list_quarantined_cards()
        for r in records:
            if r.quarantine_id == quarantine_id_or_card_id or r.card_id == quarantine_id_or_card_id:
                target_path = self.workspace_root / r.quarantined_path
                if target_path.exists():
                    try:
                        content = json.loads(target_path.read_text(encoding="utf-8"))
                        return content, r
                    except Exception:
                        return {"raw": target_path.read_text(encoding="utf-8")}, r
        return None

    def release_card(
        self,
        quarantine_id: str,
        target_dir: str = ".jobs/pending",
        re_sign: bool = True,
        attestation_engine: Optional[CardAttestationEngine] = None
    ) -> Optional[str]:
        """
        Releases a card from quarantine back to an active queue directory after authorization.
        Optionally signs with an authorized attestation engine.
        """
        inspect_res = self.inspect_quarantined_card(quarantine_id)
        if not inspect_res:
            return None

        card_dict, record = inspect_res
        if re_sign:
            engine = attestation_engine or CardAttestationEngine()
            card_dict = engine.sign_card(card_dict, issuer="AJE-Quarantine-Auditor")

        dest_dir = self.workspace_root / target_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_card_id = record.card_id.replace("/", "_").replace("\\", "_")
        dest_file = dest_dir / f"{safe_card_id}.json"

        dest_file.write_text(json.dumps(card_dict, indent=2), encoding="utf-8")

        # Cleanup quarantine files
        q_file = self.workspace_root / record.quarantined_path
        m_file = self.workspace_root / record.meta_path
        try:
            if q_file.exists():
                os.chmod(str(q_file), stat.S_IWRITE | stat.S_IREAD)
                q_file.unlink()
            if m_file.exists():
                m_file.unlink()
        except Exception:
            pass

        return str(dest_file.relative_to(self.workspace_root)).replace("\\", "/")

    def purge_quarantine(self, quarantine_id: str) -> bool:
        """Purges a single record from quarantine."""
        records = self.list_quarantined_cards()
        for r in records:
            if r.quarantine_id == quarantine_id or r.card_id == quarantine_id:
                q_file = self.workspace_root / r.quarantined_path
                m_file = self.workspace_root / r.meta_path
                try:
                    if q_file.exists():
                        os.chmod(str(q_file), stat.S_IWRITE | stat.S_IREAD)
                        q_file.unlink()
                    if m_file.exists():
                        m_file.unlink()
                    return True
                except Exception:
                    return False
        return False
