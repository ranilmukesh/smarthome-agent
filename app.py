"""
app.py
------
FastAPI application that wraps the SmartHome Agno agent.

Endpoints:
  POST /query          → main query endpoint (spec-compliant response)
  GET  /health         → liveness check
  GET  /devices        → convenience: list all device IDs from Neo4j
  (+ AgentOS endpoints: /sessions, /runs, tracing UI, etc.)

Start:
    python app.py
  or via uvicorn:
    uvicorn app:app --host 0.0.0.0 --port 7777 --reload
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("smarthome_api")

# ── Import agent (lazy — catches misconfiguration at startup, not per-request)
try:
    from agent import build_agent, _db
    _agent = build_agent()
    log.info("SmartHomeAgent initialised successfully.")
except Exception as exc:
    log.error("Failed to build agent: %s", exc)
    _agent = None  # handled gracefully in endpoint

# ── Pydantic models ───────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    """
    Request body for POST /query.
    session_id: if omitted, a new UUID session is created per call (stateless).
                Pass the same value across calls for multi-turn conversation.
    """
    question: str = Field(..., min_length=1, max_length=2000,
                          json_schema_extra={"example": "Which sensors trigger the hallway lights?"})
    include_reasoning: bool = Field(True,
                                    description="Include the agent's tool-call trace")
    session_id: Optional[str] = Field(None,
                                      description="Reuse for multi-turn conversations")


class QueryResponse(BaseModel):
    """Spec-compliant response matching the assignment schema."""
    answer: str
    reasoning_trace: list[str]
    retrieved_context: list[str]
    confidence_score: float
    session_id: str
    elapsed_ms: float


class HealthResponse(BaseModel):
    status: str
    agent_ready: bool
    neo4j_connected: bool


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Smart Home Query API",
    description=(
        "Intelligent query system for smart-home devices powered by "
        "Agno + NVIDIA NIM + Neo4j Graph RAG."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_trace_and_context(run) -> tuple[list[str], list[str]]:
    """
    Parse the Agno RunResponse to extract:
      • reasoning_trace — ordered list of tool-call summaries
      • retrieved_context — tool result snippets (≤ 500 chars each)
    """
    trace: list[str] = []
    ctx: list[str] = []

    messages = getattr(run, "messages", None) or []
    step = 1
    for msg in messages:
        role = getattr(msg, "role", "")

        # Collect tool calls (model → tool)
        for tc in getattr(msg, "tool_calls", None) or []:
            fn = tc.get("function", {})
            name = fn.get("name", "unknown_tool")
            args = str(fn.get("arguments", ""))[:200]
            trace.append(f"Step {step}: [{name}] {args}")
            step += 1

        # Collect tool results
        if role == "tool":
            snippet = str(getattr(msg, "content", ""))[:500]
            if snippet:
                ctx.append(snippet)

    return trace, ctx


def _confidence(ctx: list[str], answer: str) -> float:
    """
    Heuristic confidence score:
      0.9  → retrieved context + non-trivial answer
      0.7  → answer only (no retrieved context)
      0.4  → very short / uncertain answer
    """
    if not answer or len(answer) < 20:
        return 0.4
    if ctx:
        return 0.9
    return 0.7


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Meta"])
async def health():
    """Liveness check — also pings Neo4j."""
    neo4j_ok = False
    try:
        from neo4j import GraphDatabase
        d = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USERNAME"),
                  os.getenv("NEO4J_PASSWORD")),
        )
        d.verify_connectivity()
        with d.session(database="9d77d39a") as s:
            s.run("RETURN 1")
        d.close()
        neo4j_ok = True
    except Exception:
        pass

    return HealthResponse(
        status="ok" if (_agent and neo4j_ok) else "degraded",
        agent_ready=_agent is not None,
        neo4j_connected=neo4j_ok,
    )


@app.get("/devices", tags=["Convenience"])
async def list_devices():
    """Returns all device IDs and names from Neo4j (no agent needed)."""
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USERNAME"),
                  os.getenv("NEO4J_PASSWORD")),
        )
        with driver.session(database="9d77d39a") as s:
            result = s.run(
                "MATCH (d:Device) RETURN d.device_id AS id, d.name AS name, "
                "d.location AS location, d.state AS state ORDER BY d.location"
            )
            devices = [dict(r) for r in result]
        driver.close()
        return {"devices": devices, "count": len(devices)}
    except Exception as e:
        raise HTTPException(503, f"Neo4j unavailable: {e}")


@app.post("/query", response_model=QueryResponse, tags=["Query"])
async def query(body: QueryRequest, request: Request):
    """
    Main query endpoint.

    The Agno agent autonomously:
    1. think()       → decides Cypher vs semantic search
    2. get_schema    → grounds query generation
    3. run_cypher / knowledge search
    4. analyze()     → validates sufficiency (max 3 loops)
    5. Returns natural-language answer
    """
    if _agent is None:
        raise HTTPException(503, "Agent not initialised — check server logs.")

    session_id = body.session_id or str(uuid.uuid4())
    log.info("query | session=%s | question=%s", session_id, body.question[:80])

    t0 = time.perf_counter()
    try:
        run = await _agent.arun(body.question, session_id=session_id)
    except TimeoutError:
        raise HTTPException(504, "Agent timed out — try a simpler question.")
    except Exception as exc:
        log.exception("Agent error: %s", exc)
        raise HTTPException(502, f"Agent failure: {exc}")

    elapsed = round((time.perf_counter() - t0) * 1000, 1)

    answer = getattr(run, "content", "") or ""
    trace, ctx = _extract_trace_and_context(run)

    return QueryResponse(
        answer=answer,
        reasoning_trace=trace if body.include_reasoning else [],
        retrieved_context=ctx,
        confidence_score=_confidence(ctx, answer),
        session_id=session_id,
        elapsed_ms=elapsed,
    )


# ── AgentOS wrapper (adds /sessions, /runs, tracing UI) ──────────────────────
try:
    from agno.os import AgentOS  # type: ignore
    from agno.os.config import AgentOSConfig, EvalsConfig

    if _agent:
        os_config = AgentOSConfig(
            available_models=["nvidia:nvidia/nemotron-3-super-120b-a12b", "nvidia:meta/llama-3.3-70b-instruct"],
            evals=EvalsConfig(
                available_models=["nvidia:nvidia/nemotron-3-super-120b-a12b", "nvidia:meta/llama-3.3-70b-instruct"]
            )
        )
        _agent_os = AgentOS(
            agents=[_agent], 
            base_app=app, 
            db=_db, 
            tracing=True,
            config=os_config
        )
        app = _agent_os.get_app()
        log.info("AgentOS overlay applied — /sessions and tracing available.")
except ImportError:
    log.warning("agno.os not available — AgentOS features disabled.")
except Exception as exc:
    log.warning("AgentOS setup failed (non-fatal): %s", exc)


# ── Dev entrypoint ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", 7777)),
        reload=True,
        log_level="info",
    )
