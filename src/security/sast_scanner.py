"""AST SAST Security & CVE Supply-Chain Scanner
AJE v4.0 Security Matrix Component

Performs Static Application Security Testing (SAST) via Python AST traversal
to catch dangerous dynamic execution (eval/exec), unsafe shell spawns (shell=True),
insecure deserialization (pickle/yaml), hardcoded credentials, and known
vulnerable/typosquatted supply chain dependencies before code enters production.
"""

import ast
import re
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
from pathlib import Path


class SASTRiskLevel(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class SASTFinding:
    rule_id: str
    category: str
    cwe: str
    risk_level: SASTRiskLevel
    message: str
    file_path: str
    line_number: int
    column: int
    snippet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "cwe": self.cwe,
            "risk_level": self.risk_level.value,
            "message": self.message,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column": self.column,
            "snippet": self.snippet,
        }


@dataclass
class SASTScanResult:
    passed: bool
    highest_risk: SASTRiskLevel
    findings: List[SASTFinding] = field(default_factory=list)
    scanned_files_count: int = 0
    violations_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "highest_risk": self.highest_risk.value,
            "findings": [f.to_dict() for f in self.findings],
            "scanned_files_count": self.scanned_files_count,
            "violations_count": self.violations_count,
        }


class _ASTSecurityVisitor(ast.NodeVisitor):
    """Internal AST visitor that inspects nodes for security vulnerabilities."""

    SECRET_PATTERNS = [
        (r"(?i)(api_key|apikey|secret_key|auth_token|access_token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-+/]{16,}['\"]", "Hardcoded credential token"),
        (r"-----BEGIN (RSA|EC|OPENSSH|DSA|PRIVATE) KEY-----", "Hardcoded private key certificate"),
        (r"ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|glpat-[A-Za-z0-9\-]{20}", "Hardcoded GitHub/GitLab access token"),
        (r"AKIA[0-9A-Z]{16}", "Hardcoded AWS Access Key ID"),
    ]

    def __init__(self, file_path: str, source_code: str):
        self.file_path = file_path
        self.source_code = source_code
        self.source_lines = source_code.splitlines()
        self.findings: List[SASTFinding] = []

    def _get_snippet(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def visit_Call(self, node: ast.Call):
        # 1. Dangerous dynamic execution: eval(), exec(), compile()
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        lineno = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0)
        snippet = self._get_snippet(lineno)

        if func_name in ("eval", "exec"):
            self.findings.append(SASTFinding(
                rule_id="AJE-SAST-001",
                category="Code Injection",
                cwe="CWE-95",
                risk_level=SASTRiskLevel.CRITICAL,
                message=f"Use of dangerous dynamic execution function '{func_name}()'.",
                file_path=self.file_path,
                line_number=lineno,
                column=col,
                snippet=snippet,
            ))

        # 2. Insecure shell commands: os.system(), os.popen()
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                if node.func.attr in ("system", "popen", "popen2", "popen3", "popen4"):
                    self.findings.append(SASTFinding(
                        rule_id="AJE-SAST-002",
                        category="Command Injection",
                        cwe="CWE-78",
                        risk_level=SASTRiskLevel.CRITICAL,
                        message=f"Direct invocation of insecure shell command 'os.{node.func.attr}()'.",
                        file_path=self.file_path,
                        line_number=lineno,
                        column=col,
                        snippet=snippet,
                    ))

        # 3. Subprocess with shell=True
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                for kw in node.keywords:
                    if kw.arg == "shell":
                        # Check if shell is True
                        if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            self.findings.append(SASTFinding(
                                rule_id="AJE-SAST-003",
                                category="Command Injection",
                                cwe="CWE-78",
                                risk_level=SASTRiskLevel.HIGH,
                                message="Subprocess invoked with 'shell=True', exposing command injection risk.",
                                file_path=self.file_path,
                                line_number=lineno,
                                column=col,
                                snippet=snippet,
                            ))

        # 4. Insecure deserialization: pickle.loads/load, yaml.load without SafeLoader
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            mod_name = node.func.value.id
            method_name = node.func.attr
            if mod_name == "pickle" and method_name in ("load", "loads"):
                self.findings.append(SASTFinding(
                    rule_id="AJE-SAST-004",
                    category="Insecure Deserialization",
                    cwe="CWE-502",
                    risk_level=SASTRiskLevel.HIGH,
                    message="Unsafe deserialization using 'pickle' module.",
                    file_path=self.file_path,
                    line_number=lineno,
                    column=col,
                    snippet=snippet,
                ))
            elif mod_name == "yaml" and method_name in ("load", "unsafe_load"):
                # Check Loader keyword
                is_safe_loader = False
                for kw in node.keywords:
                    if kw.arg == "Loader":
                        if isinstance(kw.value, ast.Attribute) and kw.value.attr in ("SafeLoader", "CSafeLoader"):
                            is_safe_loader = True
                if not is_safe_loader and method_name == "load":
                    self.findings.append(SASTFinding(
                        rule_id="AJE-SAST-005",
                        category="Insecure Deserialization",
                        cwe="CWE-502",
                        risk_level=SASTRiskLevel.HIGH,
                        message="Unsafe YAML loading without explicit SafeLoader.",
                        file_path=self.file_path,
                        line_number=lineno,
                        column=col,
                        snippet=snippet,
                    ))

        # 5. Insecure temp file generation: tempfile.mktemp()
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "tempfile" and node.func.attr == "mktemp":
                self.findings.append(SASTFinding(
                    rule_id="AJE-SAST-006",
                    category="Insecure Temporary File",
                    cwe="CWE-377",
                    risk_level=SASTRiskLevel.MEDIUM,
                    message="Insecure temp file creation with 'tempfile.mktemp()' (race condition prone). Use 'NamedTemporaryFile' or 'mkstemp'.",
                    file_path=self.file_path,
                    line_number=lineno,
                    column=col,
                    snippet=snippet,
                ))

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        # 6. Hardcoded Secret & Credential Detection in AST String Literals
        if isinstance(node.value, str) and len(node.value) >= 12:
            val = node.value
            lineno = getattr(node, "lineno", 1)
            col = getattr(node, "col_offset", 0)
            snippet = self._get_snippet(lineno)

            for pattern, desc in self.SECRET_PATTERNS:
                if re.search(pattern, val) or re.search(pattern, snippet):
                    self.findings.append(SASTFinding(
                        rule_id="AJE-SAST-007",
                        category="Hardcoded Secrets",
                        cwe="CWE-798",
                        risk_level=SASTRiskLevel.HIGH,
                        message=f"{desc} detected in AST literal.",
                        file_path=self.file_path,
                        line_number=lineno,
                        column=col,
                        snippet=snippet[:80],
                    ))
                    break

        self.generic_visit(node)


class SupplyChainScanner:
    """Scans package imports and dependency manifests for known insecure/deprecated packages."""

    PROHIBITED_OR_DEPRECATED_PACKAGES = {
        "pycrypto": "Deprecated and unmaintained cryptographic library; use 'cryptography'.",
        "crypto": "Ambiguous/often confused package name; use 'cryptography'.",
        "telnetlib": "Insecure unencrypted remote protocol.",
        "pylint-runner": "Deprecated runner wrapper.",
        "pyyaml-yaml": "Known typosquat target.",
    }

    def scan_imports(self, source_code: str, file_path: str = "memory") -> List[SASTFinding]:
        findings: List[SASTFinding] = []
        try:
            tree = ast.parse(source_code)
        except Exception:
            return findings

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg_root = alias.name.split(".")[0].lower()
                    if pkg_root in self.PROHIBITED_OR_DEPRECATED_PACKAGES:
                        findings.append(SASTFinding(
                            rule_id="AJE-SUPPLY-001",
                            category="Supply Chain Risk",
                            cwe="CWE-1395",
                            risk_level=SASTRiskLevel.HIGH,
                            message=f"Insecure or deprecated dependency '{pkg_root}': {self.PROHIBITED_OR_DEPRECATED_PACKAGES[pkg_root]}",
                            file_path=file_path,
                            line_number=getattr(node, "lineno", 1),
                            column=getattr(node, "col_offset", 0),
                        ))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg_root = node.module.split(".")[0].lower()
                    if pkg_root in self.PROHIBITED_OR_DEPRECATED_PACKAGES:
                        findings.append(SASTFinding(
                            rule_id="AJE-SUPPLY-001",
                            category="Supply Chain Risk",
                            cwe="CWE-1395",
                            risk_level=SASTRiskLevel.HIGH,
                            message=f"Insecure or deprecated dependency '{pkg_root}': {self.PROHIBITED_OR_DEPRECATED_PACKAGES[pkg_root]}",
                            file_path=file_path,
                            line_number=getattr(node, "lineno", 1),
                            column=getattr(node, "col_offset", 0),
                        ))
        return findings


class ASTSecurityScanner:
    """Enterprise AST Static Application Security Testing (SAST) Scanner."""

    def __init__(self, block_on_critical: bool = True, block_on_high: bool = True):
        self.block_on_critical = block_on_critical
        self.block_on_high = block_on_high
        self.supply_chain = SupplyChainScanner()

    def scan_source(self, source_code: str, file_path: str = "in_memory.py") -> SASTScanResult:
        """Parses and scans a single Python source string."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            # Return finding for syntax error
            finding = SASTFinding(
                rule_id="AJE-SAST-PARSE",
                category="Syntax Error",
                cwe="CWE-20",
                risk_level=SASTRiskLevel.LOW,
                message=f"Could not parse Python source AST: {str(e)}",
                file_path=file_path,
                line_number=getattr(e, "lineno", 1) or 1,
                column=getattr(e, "offset", 0) or 0,
            )
            return SASTScanResult(
                passed=True,
                highest_risk=SASTRiskLevel.LOW,
                findings=[finding],
                scanned_files_count=1,
                violations_count=1,
            )

        visitor = _ASTSecurityVisitor(file_path=file_path, source_code=source_code)
        visitor.visit(tree)

        # Supply chain checks
        supply_findings = self.supply_chain.scan_imports(source_code, file_path=file_path)
        all_findings = visitor.findings + supply_findings

        # Determine highest risk
        highest_risk = SASTRiskLevel.INFO
        if any(f.risk_level == SASTRiskLevel.CRITICAL for f in all_findings):
            highest_risk = SASTRiskLevel.CRITICAL
        elif any(f.risk_level == SASTRiskLevel.HIGH for f in all_findings):
            highest_risk = SASTRiskLevel.HIGH
        elif any(f.risk_level == SASTRiskLevel.MEDIUM for f in all_findings):
            highest_risk = SASTRiskLevel.MEDIUM
        elif any(f.risk_level == SASTRiskLevel.LOW for f in all_findings):
            highest_risk = SASTRiskLevel.LOW

        passed = True
        if self.block_on_critical and highest_risk == SASTRiskLevel.CRITICAL:
            passed = False
        if self.block_on_high and highest_risk == SASTRiskLevel.HIGH:
            passed = False

        return SASTScanResult(
            passed=passed,
            highest_risk=highest_risk,
            findings=all_findings,
            scanned_files_count=1,
            violations_count=len(all_findings),
        )

    def scan_directory(self, root_dir: str, exclude_dirs: Optional[List[str]] = None) -> SASTScanResult:
        """Scans all Python files in a directory recursively."""
        root = Path(root_dir).resolve()
        exclude = set(exclude_dirs or [".git", ".jobs", "__pycache__", ".venv", "venv", "node_modules", "notebook_workspace"])

        all_findings: List[SASTFinding] = []
        files_scanned = 0

        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if parts.intersection(exclude):
                continue

            try:
                content = path.read_text(encoding="utf-8")
                rel_path = str(path.relative_to(root)).replace("\\", "/")
                res = self.scan_source(content, file_path=rel_path)
                files_scanned += 1
                all_findings.extend(res.findings)
            except Exception:
                continue

        highest_risk = SASTRiskLevel.INFO
        if any(f.risk_level == SASTRiskLevel.CRITICAL for f in all_findings):
            highest_risk = SASTRiskLevel.CRITICAL
        elif any(f.risk_level == SASTRiskLevel.HIGH for f in all_findings):
            highest_risk = SASTRiskLevel.HIGH
        elif any(f.risk_level == SASTRiskLevel.MEDIUM for f in all_findings):
            highest_risk = SASTRiskLevel.MEDIUM
        elif any(f.risk_level == SASTRiskLevel.LOW for f in all_findings):
            highest_risk = SASTRiskLevel.LOW

        passed = True
        if self.block_on_critical and highest_risk == SASTRiskLevel.CRITICAL:
            passed = False
        if self.block_on_high and highest_risk == SASTRiskLevel.HIGH:
            passed = False

        return SASTScanResult(
            passed=passed,
            highest_risk=highest_risk,
            findings=all_findings,
            scanned_files_count=files_scanned,
            violations_count=len(all_findings),
        )
