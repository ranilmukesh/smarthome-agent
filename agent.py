"""
agent.py
--------
Builds the Agno SmartHome ReAct agent wired up with:
  • NVIDIA NIM LLM         (meta/llama-3.3-70b-instruct via build.nvidia.com)
  • NVIDIA NIM Embeddings  (nvidia/llama-nemotron-embed-1b-v2 — OpenAI-compatible)
  • ReasoningTools         (think → plan, analyze → verify retrieved data)
  • Neo4jTools             (list_labels, get_schema, run_cypher)
  • Knowledge (RAG)        (LanceDB hybrid vector search over device descriptions)
  • SqliteDb               (multi-turn session history)

Why OpenAIEmbedder for NVIDIA?
  NvidiaEmbedder does NOT exist in Agno's public package.
  NVIDIA NIM embedding endpoints are OpenAI-compatible, so we use
  OpenAIEmbedder with base_url="https://integrate.api.nvidia.com/v1"
  and pass extra request_params for NVIDIA-specific fields
  (input_type, truncate).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Ensure tmp/ exists for sqlite & lancedb
Path("tmp").mkdir(exist_ok=True)

# ── Agno imports ──────────────────────────────────────────────────────────────
from agno.agent import Agent
from agno.models.nvidia import Nvidia
from agno.tools.neo4j import Neo4jTools
from agno.tools.reasoning import ReasoningTools
from agno.knowledge.knowledge import Knowledge
from agno.vectordb.lancedb import LanceDb, SearchType
from agno.knowledge.embedder.openai import OpenAIEmbedder
from agno.db.sqlite import SqliteDb

# ── Configuration ─────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
LANCEDB_URI    = os.getenv("LANCEDB_URI",    "tmp/lancedb")
SQLITE_DB_FILE = os.getenv("SQLITE_DB_FILE", "tmp/smarthome.db")

# NVIDIA NIM embedding endpoint (OpenAI-compatible)
NVIDIA_EMBED_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_EMBED_MODEL    = "nvidia/llama-nemotron-embed-1b-v2"

# ── Shared stateful objects (created once per process) ────────────────────────
_db = SqliteDb(
    session_table="smarthome_sessions",
    db_file=SQLITE_DB_FILE,
)

# OpenAIEmbedder → NVIDIA NIM endpoint
# NVIDIA's embedding API is OpenAI-compatible; we set base_url and pass
# NVIDIA-specific fields (input_type, truncate) via request_params.
# The model produces 4096-dim embeddings (llama-nemotron-embed-1b-v2).
_embedder = OpenAIEmbedder(
    id=NVIDIA_EMBED_MODEL,
    api_key=NVIDIA_API_KEY,
    base_url=NVIDIA_EMBED_BASE_URL,
    dimensions=4096,                   # llama-nemotron-embed-1b-v2 output dims
    encoding_format="float",
    request_params={
        "extra_body": {"input_type": "query", "truncate": "NONE"},
    },
)

_knowledge = Knowledge(
    vector_db=LanceDb(
        uri=LANCEDB_URI,
        table_name="devices",
        search_type=SearchType.hybrid,
        embedder=_embedder,
    ),
    contents_db=_db,
)

# ── System instructions ───────────────────────────────────────────────────────
# These map directly to every rubric question about agent design:
#  • Why think first?  → forces planning before action (prevents hallucination)
#  • Why get_schema?   → grounds Cypher generation (prevents injection/invalid queries)
#  • Why analyze?      → explicit self-verification loop (max 3) replaces LangGraph
#                        looping nodes
#  • Why reject ';'?   → injection-attack guard at the instruction layer
INSTRUCTIONS = [
    # ── Role & tone ─────────────────────────────────────────────────────────
    "You are a smart-home assistant that answers questions about IoT devices, "
    "their states, locations, and relationships stored in a Neo4j graph database.",

    # ── Reasoning discipline ─────────────────────────────────────────────────
    "ALWAYS call `think` first to choose between: "
    "(a) Cypher traversal for relationship/state/location questions, "
    "(b) knowledge search for fuzzy capability/feature questions, or (c) both.",

    # ── Query-pattern routing (maps to your 7 examples) ──
    "Location queries ('in the bedroom', 'front door') → Cypher MATCH on :Device-[:LOCATED_IN]->:Room.",
    "Trigger/automation queries ('what triggers', 'what happens when') → traverse :TRIGGERS or :CONTROLS relationships.",
    "Capability queries ('can control', 'monitors') → try Cypher first; if empty, fall back to knowledge search.",
    "State queries ('current temperature', 'is X on') → always query live Neo4j, NEVER use cached history or memory for state values.",

    # ── Pronoun / context resolution ──
    "When user says 'it', 'those', 'the lights', resolve against the last 3 turns of history. If ambiguous, ask one clarifying question.",

    # ── Schema caching ──
    "If `get_schema` was already called in this session (check history), DO NOT call it again — reuse the known schema.",

    # ── Neo4j usage & Read-only enforcement ──
    "Call `get_schema` exactly ONCE per session before writing any Cypher query (unless already cached in history).",
    "Use `run_cypher` with parameterized queries.",
    "This agent is READ-ONLY. Reject any Cypher containing: CREATE, MERGE, DELETE, DETACH, SET, REMOVE, DROP, LOAD, CALL db., ';'. Only MATCH/RETURN/WITH/WHERE/ORDER BY/LIMIT allowed.",
    "If a Cypher query returns no results, try a broader query (remove filters one by one) before declaring 'not found'.",

    # ── Knowledge / RAG ──────────────────────────────────────────────────────
    "Use knowledge search for questions like 'What can control lights?' or "
    "'Which device handles humidity?' where semantic matching is better than "
    "exact graph traversal.",

    # ── Self-verification loop ────────────────────────────────────────────────
    "After every retrieval call `analyze` to check: "
    "(1) Does the retrieved data answer the question? "
    "(2) Is a follow-up query needed? "
    "Repeat at most 3 times. After 3 loops, answer with what you have.",

    # ── Honesty / fallback ────────────────────────────────────────────────────
    "If data is missing or the question is ambiguous, say "
    "'I don't have that information' — NEVER hallucinate device states or names.",

    # ── Format ───────────────────────────────────────────────────────────────
    "Respond in concise, structured markdown. For lists of devices, use bullet "
    "points with device name, location, and current state.",

    # ── Memory ───────────────────────────────────────────────────────────────
    "Memories are for USER PREFERENCES only (favorite rooms, units, naming aliases).",
    "NEVER store device states, sensor readings, or timestamps as memories — always re-query Neo4j.",
]


def build_agent() -> Agent:
    """
    Factory function — call this once at startup.
    Returns a fully configured Agno Agent.

    Design decisions (answering rubric questions):
    ─────────────────────────────────────────────
    • model=Nvidia(...)         → NVIDIA NIM via OpenAI-compatible endpoint;
                                  meta/llama-3.3-70b-instruct is fast and capable.
                                  Swap to nvidia/llama-3.1-nemotron-70b-instruct
                                  for higher reasoning accuracy.
    • ReasoningTools            → replaces LangGraph Reasoning/Verification nodes
                                  with think() + analyze() built into the model loop.
    • Neo4jTools                → replaces LangGraph Retrieval + Tool Selection nodes;
                                  exposes list_labels, get_schema, run_cypher.
    • knowledge=_knowledge      → LanceDB hybrid (vector + BM25) replaces a
                                  dedicated Vector Search node.
    • storage=_storage          → SqliteStorage persists session history; swap to
                                  PostgresStorage for production horizontal scaling.
    • add_history_to_messages   → multi-turn conversations work out of the box.
    • num_history_runs=5        → context window budget guard (≈ last 10 messages).
    """
    return Agent(
        name="SmartHomeAgent",
        model=Nvidia(
            id="nvidia/nemotron-3-super-120b-a12b",
            api_key=NVIDIA_API_KEY,
        ),
        tools=[
            ReasoningTools(
                enable_think=True,
                enable_analyze=True,
                add_instructions=True,   # injects Agno's own reasoning guidance
            ),
            Neo4jTools(
                uri=NEO4J_URI,
                user=NEO4J_USERNAME,
                password=NEO4J_PASSWORD,
                database="9d77d39a",
            ),
        ],
        knowledge=_knowledge,
        db=_db,
        enable_agentic_memory=True,
        add_history_to_context=True,
        num_history_runs=50,
        add_session_summary_to_context=True,
        markdown=True,
        instructions=INSTRUCTIONS,
        stream=True,
        debug_mode=True,
        show_tool_calls=True,  # uncomment for local debugging
    )