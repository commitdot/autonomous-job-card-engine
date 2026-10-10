import subprocess
import os
import re
import shlex
from typing import List, Dict, Any, Optional


def extract_fault_frame(output: str, max_lines: int = 35) -> str:
    """
    Parses validation/test output and extracts fault-localized traceback frames,
    pruning redundant framework boilerplate and verbose logs.

    Extracts:
    - Traceback error frames and failing lines (e.g., File "...", line ..., in ...)
    - Assertion failures and exception error lines (e.g., AssertionError, SyntaxError, etc.)
    - Pytest / unittest failure summaries (e.g., FAILED test_foo.py::test_bar)
    """
    if not output:
        return ""

    lines = output.splitlines()

    extracted: List[str] = []
    capture = False
    in_traceback = False

    for line in lines:
        stripped = line.strip()

        # Detect start of traceback or failure block
        if "Traceback (most recent call last):" in line or line.startswith("FAILED ") or "=== FAILURES ===" in line:
            capture = True
            in_traceback = True
            extracted.append(line)
            continue

        if capture:
            # Capture traceback frames, code lines, errors, assertion differences
            if (
                line.startswith("  File ")
                or line.startswith("    ")
                or line.startswith(">   ")
                or line.startswith("E   ")
                or line.startswith("AssertionError")
                or line.startswith("TypeError")
                or line.startswith("ValueError")
                or line.startswith("AttributeError")
                or line.startswith("KeyError")
                or line.startswith("NameError")
                or line.startswith("ImportError")
                or line.startswith("SyntaxError")
                or "FAILED " in line
                or "short test summary info" in line
            ):
                extracted.append(line)
            elif stripped.startswith("===") or stripped.startswith("---"):
                extracted.append(line)
                if "short test summary info" not in line and len(extracted) > 10:
                    # Possible end of failure block
                    pass

        # If no explicit traceback was found, capture lines with error/fail markers
        if not capture:
            if any(marker in line.lower() for marker in ["error:", "failed", "exception:", "assertionerror", "syntaxerror", "blocked:"]):
                extracted.append(line)

    if not extracted:
        # Fall back to head and tail of output
        return "\n".join(lines[:10] + ["\n... [PRUNED BOILERPLATE OUTPUT] ...\n"] + lines[-20:])

    if len(extracted) > max_lines:
        return "\n".join(extracted[:max_lines // 2] + ["\n... [PRUNED INTERMEDIATE FRAMES] ...\n"] + extracted[-max_lines // 2:])

    return "\n".join(extracted)


class SandboxRunner:
    def __init__(self, banned_commands: List[str] = None):
        self.banned_commands = banned_commands or ["rm -rf", "docker system prune"]

    def is_safe_command(self, cmd: str) -> bool:
        """
        Verify that the command does not violate any safety policies or banned execution rules.
        """
        cmd_normalized = cmd.strip().lower()
        for banned in self.banned_commands:
            if banned.strip().lower() in cmd_normalized:
                return False
        return True

    def run_validation(self, cmd: str, cwd: str = ".", timeout_seconds: int = 30) -> Dict[str, Any]:
        """
        Executes a validation command inside the workspace and captures exit code, raw output,
        and fault-localized traceback frames.
        """
        if not self.is_safe_command(cmd):
            return {
                "success": False,
                "exit_code": -1,
                "output": f"BLOCKED: Command '{cmd}' violated Sandbox Safety Policy!",
                "fault_frame": f"BLOCKED: Command '{cmd}' violated Sandbox Safety Policy!",
                "flaky": False,
            }

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            raw_out = (result.stdout or "") + "\n" + (result.stderr or "")
            fault = extract_fault_frame(raw_out) if result.returncode != 0 else ""
            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "output": raw_out,
                "fault_frame": fault or raw_out[:500],
                "flaky": False,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -2,
                "output": f"ERROR: Command execution timed out ({timeout_seconds}s limit).",
                "fault_frame": f"ERROR: Command execution timed out ({timeout_seconds}s limit).",
                "flaky": False,
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -3,
                "output": f"ERROR: Execution failed: {str(e)}",
                "fault_frame": f"ERROR: Execution failed: {str(e)}",
                "flaky": False,
            }

    def run_validation_with_flakiness_check(
        self, cmd: str, cwd: str = ".", timeout_seconds: int = 30
    ) -> Dict[str, Any]:
        """
        Dual-Pass Flakiness Detector:
        If a validation command fails, executes an immediate re-run without code mutation.
        If the second run passes, flags the test as 'flaky' to prevent runaway self-healing loops.
        """
        initial_result = self.run_validation(cmd, cwd=cwd, timeout_seconds=timeout_seconds)
        if initial_result["success"]:
            return initial_result

        # Re-run immediately for non-deterministic flakiness detection
        second_result = self.run_validation(cmd, cwd=cwd, timeout_seconds=timeout_seconds)
        if second_result["success"]:
            # Test succeeded on re-run: Non-deterministic flakiness!
            return {
                "success": True,
                "exit_code": 0,
                "output": second_result["output"],
                "fault_frame": "[FLAKY TEST DETECTED] Test passed on second execution without code mutation.",
                "flaky": True,
            }

        # Deterministic failure
        return initial_result

    def run_composite_integration_gate(
        self, commands: List[str], cwd: str = "."
    ) -> Dict[str, Any]:
        """
        Runs composite integration gate suite (type checks, integration tests, contract assertions).
        All commands must pass for the gate to succeed.
        """
        results = []
        for cmd in commands:
            res = self.run_validation_with_flakiness_check(cmd, cwd=cwd)
            results.append({"command": cmd, "result": res})
            if not res["success"]:
                return {
                    "passed": False,
                    "failed_command": cmd,
                    "results": results,
                    "fault_frame": res.get("fault_frame", res.get("output", "")),
                }

        return {
            "passed": True,
            "failed_command": None,
            "results": results,
            "fault_frame": "",
        }
