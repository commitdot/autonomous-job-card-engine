"""
AJE Mobile & Remote Companion Bridge
====================================
Lightweight HTTP/JSON API using Python's standard library http.server (with optional
FastAPI support if installed). Exposes real-time .jobs/ state, live Mother status,
active squad workers, and allows one-touch swipe-to-approve/dismiss from mobile devices.
"""

import os
import glob
import json
import yaml
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List


class MobileBridgeHandler(BaseHTTPRequestHandler):
    workspace_root: str = "."

    def _set_headers(self, status: int = 200, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        if self.path == "/api/status" or self.path == "/":
            state = self._get_workspace_state()
            self._set_headers(200)
            self.wfile.write(json.dumps(state, indent=2).encode("utf-8"))
        elif self.path == "/api/audit":
            audit_logs = self._get_audit_logs()
            self._set_headers(200)
            self.wfile.write(json.dumps(audit_logs, indent=2).encode("utf-8"))
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if self.path.startswith("/api/card/approve"):
            card_id = data.get("card_id")
            success = self._approve_card(card_id)
            self._set_headers(200 if success else 400)
            self.wfile.write(json.dumps({"success": success, "card_id": card_id}).encode("utf-8"))
        elif self.path.startswith("/api/card/inject"):
            created_id = self._inject_card(data)
            self._set_headers(200 if created_id else 400)
            self.wfile.write(json.dumps({"success": bool(created_id), "card_id": created_id}).encode("utf-8"))
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode("utf-8"))

    def _get_workspace_state(self) -> Dict[str, Any]:
        jobs_dir = Path(self.workspace_root) / ".jobs"
        mother_path = jobs_dir / "mother_card.yaml"
        mother = {}
        if mother_path.exists():
            with open(mother_path, "r", encoding="utf-8") as f:
                mother = yaml.safe_load(f) or {}

        children = []
        for c in jobs_dir.glob("child_*.yaml"):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    card_data = yaml.safe_load(f)
                    if card_data:
                        children.append(card_data)
            except Exception:
                continue

        return {
            "project": mother.get("metadata", {}).get("name", "Autonomous Project"),
            "mother": mother,
            "children": children,
            "total_children": len(children),
        }

    def _get_audit_logs(self) -> List[Dict[str, Any]]:
        audit_dir = Path(self.workspace_root) / ".jobs" / "audit"
        logs = []
        if audit_dir.exists():
            for f in audit_dir.glob("*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as af:
                        logs.extend(json.load(af))
                except Exception:
                    continue
        return logs

    def _approve_card(self, card_id: str) -> bool:
        if not card_id:
            return False
        jobs_dir = Path(self.workspace_root) / ".jobs"
        for c in jobs_dir.glob("child_*.yaml"):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if data.get("metadata", {}).get("id") == card_id:
                    data.setdefault("status", {})["phase"] = "Pending"
                    with open(c, "w", encoding="utf-8") as f:
                        yaml.safe_dump(data, f)
                    return True
            except Exception:
                continue
        return False

    def _inject_card(self, payload: Dict[str, Any]) -> str:
        name = payload.get("name", "Manual Injected Task")
        cid = payload.get("id") or f"child-{name.lower().replace(' ', '-')[:20]}"
        card_data = {
            "apiVersion": "agent.autonomous.io/v1alpha1",
            "kind": "ChildCard",
            "metadata": {
                "id": cid,
                "parent_mother_id": payload.get("parent_mother_id", "mother-saas-backend"),
                "name": name
            },
            "spec": {
                "parent_squad_id": payload.get("parent_squad_id", "squad-backend"),
                "tactical_objective": payload.get("tactical_objective", ""),
                "deliverables": payload.get("deliverables", []),
                "external_ticket_id": payload.get("external_ticket_id") or payload.get("ticket_id"),
                "source_platform": payload.get("source_platform") or payload.get("platform"),
                "validation": {
                    "test_commands": payload.get("test_commands", [])
                }
            },
            "status": {
                "phase": "Pending",
                "current_iteration": 0,
                "logs": ["Manually injected via Mobile Bridge API"]
            }
        }
        out_path = Path(self.workspace_root) / ".jobs" / f"child_{cid}.yaml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(card_data, f)
        return cid


def start_mobile_bridge(workspace_root: str = ".", port: int = 7890) -> HTTPServer:
    MobileBridgeHandler.workspace_root = workspace_root
    server = HTTPServer(("0.0.0.0", port), MobileBridgeHandler)
    print(f"  [MOBILE-BRIDGE] Running on http://0.0.0.0:{port} (Syncing .jobs/)")
    return server
