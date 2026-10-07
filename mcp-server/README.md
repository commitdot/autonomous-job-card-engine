# AJE MCP Server

An MCP (Model Context Protocol) server that exposes **live repository intelligence tools** to the Autonomous Job-Card Engine. Instead of guessing what to build next from a static file tree, the Mother Card calls these tools to get real signals — open issues, failing tests, coverage gaps, security alerts — and generates precisely targeted successor ChildCards.

## Tools exposed

| Tool | Signal source | What it returns |
|---|---|---|
| `get_open_issues` | GitHub Issues API | Open bugs + enhancement requests |
| `get_enterprise_tickets` | ServiceNow / Salesforce / Jira / Pega | High-priority incidents and defect tickets |
| `get_failing_tests` | pytest `--json-report` output | Test functions that failed in last run |
| `get_coverage_report` | `coverage.py` JSON report | Modules below the coverage threshold |
| `get_outdated_deps` | `pip list --outdated --json` | Dependencies with newer versions available |
| `get_security_alerts` | `pip-audit --json` | Known CVEs in installed packages |
| `scan_todo_comments` | Repo file scan (grep) | All `TODO` / `FIXME` comments + file locations |
| `get_pr_review_feedback` | GitHub Pull Requests API | Unresolved review comments on open PRs |

## Installation

```bash
cd mcp-server
pip install -e .
```

## Running the server

```bash
# stdio transport (default — used by AJE engine directly)
python -m aje_mcp_server.server

# or via the installed script
aje-mcp-server
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_TOKEN` | For GitHub tools | Personal access token or GitHub App token |
| `GITHUB_REPO` | For GitHub tools | `owner/repo` slug, e.g. `commitdot/autonomous-job-card-engine` |
| `AJE_WORKSPACE` | For file-scan tools | Absolute path to the workspace root being managed |
| `SERVICENOW_INSTANCE` | Optional (ServiceNow) | ServiceNow instance slug (e.g. `dev12345`) |
| `SERVICENOW_USER` / `_PASSWORD` | Optional (ServiceNow) | Basic authentication credentials |
| `SALESFORCE_INSTANCE_URL` / `_AUTH_TOKEN` | Optional (Salesforce) | Salesforce REST API endpoint & Bearer token |
| `COVERAGE_THRESHOLD` | Optional | Minimum acceptable coverage % (default: `80`) |

## Wiring into AJE gap analysis

```python
from src.mcp_client import AJEMcpClient
from src.adapters import OllamaAdapter
from src.daemon import AutonomousJobCardEngine

mcp = AJEMcpClient(server_script="mcp-server/src/aje_mcp_server/server.py")
adapter = OllamaAdapter(workspace_root=".", mcp_client=mcp)
engine  = AutonomousJobCardEngine(workspace_root=".", adapter=adapter)
engine.run_one_cycle(".jobs/mother_card.yaml")
```
