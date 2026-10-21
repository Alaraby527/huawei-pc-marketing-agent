"""LangGraph autonomous runtime (runner version ``langgraph_autonomous_v1``).

This package replaces the *orchestration* layer only. Business truth keeps living in
the existing services (evidence, grounding, artifact revisions, rendering); the graph
decides which of those to call next and when to stop.

Design rules taken from the remediation package:

* The model owns task control (ask / query / write / revise / deliver). The graph does
  not re-impose a fixed intake -> recommend -> plan -> generate funnel.
* ``State`` holds JSON and stable references only: no SQLAlchemy Session, no API key,
  no catalog client, no image bytes.
* ``RuntimeContext`` holds the trusted, non-persistable collaborators.
* Missing model usage is recorded as *unknown*, never as a measured zero.
"""
from __future__ import annotations

RUNNER_VERSION = "langgraph_autonomous_v1"

__all__ = ["RUNNER_VERSION"]