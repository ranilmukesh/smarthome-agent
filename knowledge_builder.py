"""
knowledge_builder.py
---------------------
Pulls device descriptions from Neo4j and loads them into a LanceDB vector
store (hybrid search) so the Agno agent can do semantic RAG alongside
Cypher traversal.

Embedding model: nvidia/llama-nemotron-embed-1b-v2 via NVIDIA NIM
  • OpenAI-compatible endpoint: https://integrate.api.nvidia.com/v1
  • Agno class used: OpenAIEmbedder (NvidiaEmbedder does NOT exist in Agno)
  • Dimensions: 4096
  • input_type="passage" when indexing documents (use "query" for queries)

Run AFTER populate_graph.py:

    python knowledge_builder.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
LANCEDB_URI    = os.getenv("LANCEDB_URI",    "tmp/lancedb")

# NVIDIA NIM embedding config
NVIDIA_EMBED_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_EMBED_MODEL    = "nvidia/llama-nemotron-embed-1b-v2"

# Ensure tmp/ exists
Path("tmp").mkdir(exist_ok=True)


def fetch_device_descriptions(driver) -> list[dict]:
    """Return list of {id, content} dicts from Neo4j."""
    with driver.session(database="9d77d39a") as s:
        result = s.run(
            """
            MATCH (d:Device)
            RETURN d.device_id AS id,
                   d.description AS description,
                   d.name AS name,
                   d.location AS location,
                   d.device_type AS device_type
            ORDER BY d.device_id
            """
        )
        records = []
        for r in result:
            content = (
                r["description"]
                or f"{r['name']} ({r['device_type']}) located in {r['location']}"
            )
            records.append({"id": r["id"], "content": content})
        return records


def build_knowledge(records: list[dict]) -> None:
    """Embed and store device descriptions in LanceDB via Agno Knowledge."""
    try:
        from agno.knowledge.knowledge import Knowledge
        from agno.vectordb.lancedb import LanceDb, SearchType
        from agno.knowledge.embedder.openai import OpenAIEmbedder
    except ImportError as e:
        print(f"❌  Import error: {e}\n   Run: pip install agno lancedb openai")
        sys.exit(1)

    # Use OpenAIEmbedder with NVIDIA NIM endpoint (OpenAI-compatible).
    # NvidiaEmbedder does NOT exist in Agno's public package.
    # input_type="passage" is required for indexing documents with this model.
    embedder = OpenAIEmbedder(
        id=NVIDIA_EMBED_MODEL,
        api_key=NVIDIA_API_KEY,
        base_url=NVIDIA_EMBED_BASE_URL,
        dimensions=4096,
        encoding_format="float",
        request_params={
            "extra_body": {"input_type": "passage", "truncate": "NONE"},
        },
    )

    knowledge = Knowledge(
        vector_db=LanceDb(
            uri=LANCEDB_URI,
            table_name="devices",
            search_type=SearchType.hybrid,
            embedder=embedder,
        ),
    )

    print(f"Loading {len(records)} device descriptions into LanceDB …")
    knowledge.insert_many(
        name="devices",
        text_contents=[rec["content"] for rec in records],
        metadata={"source": "neo4j"},
    )

    print(f"Knowledge base built at {LANCEDB_URI}/devices")


if __name__ == "__main__":
    if not NVIDIA_API_KEY:
        print("⚠  NVIDIA_API_KEY not set — embeddings will fail.")
        sys.exit(1)

    print(f"Connecting to Neo4j at {NEO4J_URI} …")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
        driver.verify_connectivity()
    except Exception as e:
        print(f"FAILED: Cannot connect to Neo4j: {e}")
        sys.exit(1)

    records = fetch_device_descriptions(driver)
    driver.close()
    print(f"Fetched {len(records)} device descriptions from Neo4j.")

    if not records:
        print("WARNING: No devices found — run populate_graph.py first.")
        sys.exit(1)

    build_knowledge(records)
