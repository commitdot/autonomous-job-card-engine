import re
import os
from typing import List, Tuple

class PrivacyGuard:
    def __init__(self, restricted_paths: List[str] = None):
        self.restricted_paths = restricted_paths or []
        # Compile standard patterns for secret matching
        self.secret_patterns = [
            re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|bearer|credential|pwd)\s*[:=]\s*['\"][^'\"]+['\"]"),
            re.compile(r"(?i)gho_[a-zA-Z0-9]{36}"), # GitHub OAuth token
            re.compile(r"(?i)sk-[a-zA-Z0-9]{48}"), # OpenAI API Key
        ]

    def sanitize_content(self, text: str) -> str:
        """
        Scrubs potential credentials and secrets from prompt payloads before sending to an LLM.
        """
        sanitized = text
        for pattern in self.secret_patterns:
            # We replace values assigned to sensitive terms with a scrubbed notice
            def scrub_repl(match):
                match_str = match.group(0)
                # Split around assignment character (= or :)
                parts = re.split(r'([:=])', match_str, maxsplit=1)
                if len(parts) == 3:
                    return f"{parts[0]}{parts[1]} \"[SCRUBBED_BY_PRIVACY_GUARD]\""
                return "\"[SCRUBBED_BY_PRIVACY_GUARD]\""
            
            sanitized = pattern.sub(scrub_repl, sanitized)
        return sanitized

    def evaluate_routing_profile(self, files_accessed: List[str], base_mode: str) -> str:
        """
        Determines if the job must run locally because it accesses restricted paths.
        Returns 'local' or 'cloud'.
        """
        if base_mode == "local":
            return "local"

        for file_path in files_accessed:
            for restricted_pattern in self.restricted_paths:
                # Convert glob pattern to regex
                regex_pattern = re.escape(restricted_pattern).replace(r"\*", ".*")
                if re.search(regex_pattern, file_path):
                    # Force local routing due to restriction policy matching
                    return "local"
                    
        return "cloud"
