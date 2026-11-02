#!/usr/bin/env python3
"""Run the K8s Remediation Dashboard."""

import sys
import os
import uvicorn

# Add the src directory to the path so imports work
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(project_root, 'src'))
sys.path.insert(0, project_root)

from k8s_agent.dashboard.api import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info"
    )
