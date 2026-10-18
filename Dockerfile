# ============================================================================
# Autonomous Job-Card Engine (AJE) v4.0 — Production Container
# ============================================================================
FROM python:3.12-slim-bookworm

# Install system dependencies (Git is required for isolated worktree sandboxing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set up work directory
WORKDIR /app

# Configure Git user for autonomous commits and worktrees
RUN git config --global user.name "AJE Autonomous Daemon" && \
    git config --global user.email "daemon@autonomous-job-card-engine.internal" && \
    git config --global init.defaultBranch main

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy AJE engine source code and MCP server
COPY src/ ./src/
COPY docs/ ./docs/
COPY mcp-server/ ./mcp-server/
COPY README.md .

# Install MCP server package in editable mode
RUN pip install --no-cache-dir -e ./mcp-server

# Set default workspace directory mount point
ENV AJE_WORKSPACE_ROOT=/workspace
ENV PYTHONUNBUFFERED=1

# Expose Mobile Companion Bridge port
EXPOSE 7890

# Default entrypoint boots the autonomous daemon against the mounted workspace
VOLUME ["/workspace"]
WORKDIR /workspace

ENTRYPOINT ["python", "-m", "src.cli.main"]
CMD ["start"]
