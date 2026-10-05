import subprocess
import os
import shlex
from typing import List, Dict, Any

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

    def run_validation(self, cmd: str, cwd: str = ".") -> Dict[str, Any]:
        """
        Executes a validation command inside the workspace and captures exit code and output.
        """
        if not self.is_safe_command(cmd):
            return {
                "success": False,
                "exit_code": -1,
                "output": f"BLOCKED: Command '{cmd}' violated Sandbox Safety Policy!"
            }

        try:
            # Run command under subprocess
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30
            )
            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "output": result.stdout + "\n" + result.stderr
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -2,
                "output": "ERROR: Command execution timed out (30s limit)."
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -3,
                "output": f"ERROR: Execution failed: {str(e)}"
            }
