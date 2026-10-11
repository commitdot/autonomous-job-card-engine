"""Inbound Prompt Injection & Adversarial Payload Neutralizer
AJE v4.0 Security Matrix Component

Compliant with OWASP Top 10 for LLM Applications (LLM01: Prompt Injection)
and MITRE ATLAS (AML.T0051 / AML.T0054 LLM Jailbreak & System Instruction Override).
Scans inbound GitHub issues, Jira tickets, card payloads, and generated code
for adversarial prompts, obfuscated base64 payloads, and delimiter escapes.
"""

import re
import base64
import unicodedata
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple


class InjectionRiskLevel(str, Enum):
    SAFE = "SAFE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class InjectionFinding:
    category: str
    pattern_name: str
    matched_snippet: str
    risk_level: InjectionRiskLevel
    description: str


@dataclass
class InjectionScanResult:
    is_safe: bool
    risk_level: InjectionRiskLevel
    findings: List[InjectionFinding] = field(default_factory=list)
    sanitized_text: Optional[str] = None
    sanitization_applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "risk_level": self.risk_level.value,
            "findings": [
                {
                    "category": f.category,
                    "pattern_name": f.pattern_name,
                    "matched_snippet": f.matched_snippet,
                    "risk_level": f.risk_level.value,
                    "description": f.description,
                }
                for f in self.findings
            ],
            "sanitization_applied": self.sanitization_applied,
        }


class InboundInjectionGuard:
    """
    Scans and neutralizes adversarial prompt injection vectors, jailbreaks,
    system delimiter escapes, and dangerous executable script wrappers.
    """

    # Direct System Prompt Overrides & Jailbreaks (OWASP LLM01 / MITRE AML.T0054)
    PROMPT_OVERRIDE_PATTERNS = [
        (r"(?i)\bignore\s+(all\s+)?(previous|prior|above|system|\w+\s+)*(instructions|directives|prompts|rules|guidelines)", "ignore_prior_instructions", InjectionRiskLevel.CRITICAL),
        (r"(?i)\bdisregard\s+(all\s+)?(previous|prior|system|\w+\s+)*(instructions|guidelines|rules|prompts)", "disregard_instructions", InjectionRiskLevel.CRITICAL),
        (r"(?i)\byou\s+are\s+now\s+(in\s+)?(DAN|developer\s+mode|unrestricted|god\s+mode|jailbroken)", "jailbreak_persona", InjectionRiskLevel.CRITICAL),
        (r"(?i)\bforget\s+(everything\s+)?(you\s+know|all\s+rules|safety\s+guidelines|system\s+instructions)", "forget_rules", InjectionRiskLevel.HIGH),
        (r"(?i)\b(system\s+prompt|system_override|bypass_safety|bypass_filter)\s*[:=]", "system_prompt_override", InjectionRiskLevel.CRITICAL),
        (r"(?i)<\s*(system|instruction|admin|developer|eval)\s*>", "xml_system_tag_injection", InjectionRiskLevel.HIGH),
        (r"(?i)\[\s*(SYSTEM_PROMPT|INSTRUCTION_OVERRIDE|ADMIN_COMMAND)\s*\]", "bracket_system_injection", InjectionRiskLevel.HIGH),
        (r"(?i)\bpretend\s+(you\s+have\s+no\s+rules|there\s+are\s+no\s+constraints|to\s+be\s+an\s+evil)", "pretend_no_rules", InjectionRiskLevel.HIGH),
    ]

    # Delimiter Escapes & Injection Vectors
    DELIMITER_ESCAPE_PATTERNS = [
        (r"```+\s*(system|override|admin|root)", "codeblock_system_escape", InjectionRiskLevel.HIGH),
        (r"</\s*(system|context|instruction|governance)\s*>", "closing_system_tag_escape", InjectionRiskLevel.HIGH),
        (r"<!--\s*SYSTEM_OVERRIDE\s*-->", "html_comment_override", InjectionRiskLevel.MEDIUM),
    ]

    # Destructive Command Injections embedded inside task descriptions
    DESTRUCTIVE_COMMAND_PATTERNS = [
        (r"(?i)\brm\s+-(rf|fr|r)\s+(/|\*|/\w+)", "rm_rf_root", InjectionRiskLevel.CRITICAL),
        (r"(?i)\b(mkfs|dd\s+if=/dev/zero\s+of=/dev/)", "destructive_disk_command", InjectionRiskLevel.CRITICAL),
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork_bomb", InjectionRiskLevel.CRITICAL),
        (r"(?i)\bcurl\s+[^\n|]+\|\s*(bash|sh|zsh|powershell)", "curl_pipe_shell", InjectionRiskLevel.CRITICAL),
        (r"(?i)\bwget\s+[^\n|]+\|\s*(bash|sh|zsh|powershell)", "wget_pipe_shell", InjectionRiskLevel.CRITICAL),
        (r"(?i)\bcurl\s+-[^\n]*--data[^\n]*@(etc/passwd|/etc/shadow|\.env)", "data_exfiltration_curl", InjectionRiskLevel.CRITICAL),
    ]

    # Hidden Unicode / Invisible Text Obfuscation
    ZERO_WIDTH_CHARS = re.compile(r"[\u200B\u200C\u200D\uFEFF\u202A\u202B\u202C\u202D\u202E]")

    def __init__(self, reject_critical: bool = True, auto_neutralize: bool = True):
        self.reject_critical = reject_critical
        self.auto_neutralize = auto_neutralize

    def _normalize_text(self, text: str) -> str:
        """Normalizes Unicode homoglyphs and NFKC representation."""
        if not text:
            return ""
        return unicodedata.normalize("NFKC", text)

    def _detect_hidden_base64_payloads(self, text: str) -> List[InjectionFinding]:
        """Scans for potential base64-encoded strings that contain prompt injection triggers."""
        findings = []
        # Find potential base64 tokens of length >= 24
        b64_matches = re.findall(r"\b[A-Za-z0-9+/]{24,}={0,2}\b", text)
        for token in b64_matches:
            try:
                decoded_bytes = base64.b64decode(token, validate=True)
                decoded_str = decoded_bytes.decode("utf-8", errors="ignore")
                if len(decoded_str) >= 10:
                    for pattern, name, level in self.PROMPT_OVERRIDE_PATTERNS:
                        if re.search(pattern, decoded_str):
                            findings.append(InjectionFinding(
                                category="Obfuscated Payload",
                                pattern_name=f"base64_obfuscated_{name}",
                                matched_snippet=f"{token[:16]}... (decodes to: {decoded_str[:30]}...)",
                                risk_level=InjectionRiskLevel.CRITICAL,
                                description=f"Base64 encoded prompt injection detected in hidden payload."
                            ))
                            break
            except Exception:
                continue
        return findings

    def scan_text(self, text: str) -> InjectionScanResult:
        """
        Scans a text string for prompt injection, jailbreaks, and destructive shell payloads.
        Returns InjectionScanResult with risk categorization and sanitized output.
        """
        if not text:
            return InjectionScanResult(is_safe=True, risk_level=InjectionRiskLevel.SAFE, findings=[], sanitized_text="", sanitization_applied=False)

        normalized = self._normalize_text(text)
        findings: List[InjectionFinding] = []

        # Check for zero-width characters (Steganographic Prompt Injection)
        zero_width_matches = self.ZERO_WIDTH_CHARS.findall(normalized)
        if len(zero_width_matches) > 3:
            findings.append(InjectionFinding(
                category="Steganography",
                pattern_name="zero_width_character_obfuscation",
                matched_snippet=f"{len(zero_width_matches)} zero-width characters detected",
                risk_level=InjectionRiskLevel.HIGH,
                description="Zero-width unicode characters detected, potentially concealing hidden prompt injection directives."
            ))

        # Check prompt override patterns
        for pattern, name, level in self.PROMPT_OVERRIDE_PATTERNS:
            match = re.search(pattern, normalized)
            if match:
                findings.append(InjectionFinding(
                    category="Prompt Injection",
                    pattern_name=name,
                    matched_snippet=match.group(0),
                    risk_level=level,
                    description=f"Direct instruction override or jailbreak pattern detected ('{match.group(0)}')."
                ))

        # Check delimiter escape patterns
        for pattern, name, level in self.DELIMITER_ESCAPE_PATTERNS:
            match = re.search(pattern, normalized)
            if match:
                findings.append(InjectionFinding(
                    category="Delimiter Escape",
                    pattern_name=name,
                    matched_snippet=match.group(0),
                    risk_level=level,
                    description=f"Markdown/XML delimiter escape pattern detected ('{match.group(0)}')."
                ))

        # Check destructive command patterns
        for pattern, name, level in self.DESTRUCTIVE_COMMAND_PATTERNS:
            match = re.search(pattern, normalized)
            if match:
                findings.append(InjectionFinding(
                    category="Destructive Command Injection",
                    pattern_name=name,
                    matched_snippet=match.group(0),
                    risk_level=level,
                    description=f"Destructive shell/system command injection detected ('{match.group(0)}')."
                ))

        # Check base64 obfuscated payloads
        b64_findings = self._detect_hidden_base64_payloads(normalized)
        findings.extend(b64_findings)

        # Determine highest risk level
        risk_level = InjectionRiskLevel.SAFE
        if any(f.risk_level == InjectionRiskLevel.CRITICAL for f in findings):
            risk_level = InjectionRiskLevel.CRITICAL
        elif any(f.risk_level == InjectionRiskLevel.HIGH for f in findings):
            risk_level = InjectionRiskLevel.HIGH
        elif any(f.risk_level == InjectionRiskLevel.MEDIUM for f in findings):
            risk_level = InjectionRiskLevel.MEDIUM
        elif any(f.risk_level == InjectionRiskLevel.LOW for f in findings):
            risk_level = InjectionRiskLevel.LOW

        is_safe = risk_level in (InjectionRiskLevel.SAFE, InjectionRiskLevel.LOW)

        # Neutralization
        sanitized_text = normalized
        sanitization_applied = False
        if findings and self.auto_neutralize:
            sanitized_text = self.neutralize(normalized)
            sanitization_applied = True

        return InjectionScanResult(
            is_safe=is_safe,
            risk_level=risk_level,
            findings=findings,
            sanitized_text=sanitized_text,
            sanitization_applied=sanitization_applied
        )

    def scan_card(self, card_dict: Dict[str, Any]) -> InjectionScanResult:
        """
        Scans all text fields within a job card payload.
        """
        all_findings: List[InjectionFinding] = []
        highest_risk = InjectionRiskLevel.SAFE

        fields_to_scan = [
            "name", "title", "description", "acceptance_criteria", "command",
            "prompt", "error_trace", "notes", "commit_message", "validation_criteria"
        ]

        sanitized_card = dict(card_dict)
        applied_any_sanitization = False

        for f_name in fields_to_scan:
            val = card_dict.get(f_name)
            if isinstance(val, str) and val.strip():
                res = self.scan_text(val)
                if res.findings:
                    all_findings.extend(res.findings)
                    if res.sanitization_applied and res.sanitized_text:
                        sanitized_card[f_name] = res.sanitized_text
                        applied_any_sanitization = True

            elif isinstance(val, list):
                new_list = []
                for item in val:
                    if isinstance(item, str) and item.strip():
                        res = self.scan_text(item)
                        if res.findings:
                            all_findings.extend(res.findings)
                            new_list.append(res.sanitized_text if res.sanitized_text else item)
                            applied_any_sanitization = True
                        else:
                            new_list.append(item)
                    else:
                        new_list.append(item)
                sanitized_card[f_name] = new_list

        if any(f.risk_level == InjectionRiskLevel.CRITICAL for f in all_findings):
            highest_risk = InjectionRiskLevel.CRITICAL
        elif any(f.risk_level == InjectionRiskLevel.HIGH for f in all_findings):
            highest_risk = InjectionRiskLevel.HIGH
        elif any(f.risk_level == InjectionRiskLevel.MEDIUM for f in all_findings):
            highest_risk = InjectionRiskLevel.MEDIUM
        elif any(f.risk_level == InjectionRiskLevel.LOW for f in all_findings):
            highest_risk = InjectionRiskLevel.LOW

        return InjectionScanResult(
            is_safe=highest_risk in (InjectionRiskLevel.SAFE, InjectionRiskLevel.LOW),
            risk_level=highest_risk,
            findings=all_findings,
            sanitized_text=None,
            sanitization_applied=applied_any_sanitization
        )

    def neutralize(self, text: str) -> str:
        """
        Defangs adversarial prompt tokens, strips zero-width chars, and wraps suspicious clauses.
        """
        if not text:
            return ""

        # Strip zero-width characters
        out = self.ZERO_WIDTH_CHARS.sub("", text)

        # Defang system XML / markdown tags
        out = re.sub(r"<\s*(/?)\s*(system|instruction|admin|developer|eval)\s*>", r"[DEFANGED_TAG:\1\2]", out, flags=re.IGNORECASE)

        # Defang prompt override keywords by inert formatting
        for pattern, name, level in self.PROMPT_OVERRIDE_PATTERNS:
            out = re.sub(pattern, r"[NEUTRALIZED_PROMPT_INJECTION: \g<0>]", out)

        # Defang destructive shell command injections
        for pattern, name, level in self.DESTRUCTIVE_COMMAND_PATTERNS:
            out = re.sub(pattern, r"[BLOCKED_COMMAND: \g<0>]", out)

        return out
