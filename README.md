# Smart Home Agentic RAG System

An intelligent query system for smart home equipment powered by **Agno**, **NVIDIA NIM**, and **Neo4j Graph RAG**.

## Architecture Overview

The system implements a **ReAct-style agentic workflow** that combines:
1.  **Graph Traversal (Neo4j)**: For precise relationship queries (e.g., "Which sensors trigger the lights?").
2.  **Semantic Search (LanceDB)**: For fuzzy capability questions (e.g., "What can control humidity?").
3.  **Reasoning Loop (Agno)**: The agent uses `think()` to plan and `analyze()` to verify retrieval quality.

### Tech Stack
- **Agent Framework**: Agno (with ReasoningTools)
- **LLM**: NVIDIA NIM (`nvidia/nemotron-3-super-120b-a12b`)
- **Embeddings**: NVIDIA NIM (`nvidia/llama-nemotron-embed-1b-v2`)
- **Graph Database**: Neo4j (Relationships & State)
- **Vector Database**: LanceDB (Hybrid search over device descriptions)
- **Storage**: SQLite (Session history & state)
- **API**: FastAPI

## Setup Instructions

### 1. Environment Configuration
Create a `.env` file based on `.env.sample`:
```env
NVIDIA_API_KEY=your_nvidia_api_key
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
```

### 2. Populate Knowledge Graph
Run the seeding script to create 17 device nodes and ~25 relationships:
```bash
python populate_graph.py
```

### 3. Build Vector Knowledge Base
Embed device descriptions into LanceDB for semantic search:
```bash
python knowledge_builder.py
```

### 4. Start the API
Launch the FastAPI server:
```bash
python app.py
```

## API Specification

### POST `/query`
**Request:**
```json
{
  "question": "Which sensors trigger the hallway lights?",
  "include_reasoning": true
}
```

**Response:**
```json
{
  "answer": "The motion sensor in the hallway triggers the lights in the living room...",
  "reasoning_trace": ["Step 1: [run_cypher] ...", "Step 2: [analyze] ..."],
  "retrieved_context": ["..."],
  "confidence_score": 0.92,
  "session_id": "...",
  "elapsed_ms": 1250.5
}
```

## Implementation Notes
- **Hybrid Search**: We use LanceDB's hybrid mode (Vector + BM25) to ensure high-quality retrieval.
- **Safety**: Cypher queries are guarded against injection at the instruction layer.
- **Explainability**: The `reasoning_trace` provides step-by-step insight into the agent's decision-making.
