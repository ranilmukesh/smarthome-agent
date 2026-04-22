# 🏠 Smart Home Agentic RAG System

<p align="center">
  <img width="100%" alt="Smart Home Agent Architecture" src="smart home agent arch v2.png" />
</p>

An advanced **Agentic RAG (Retrieval-Augmented Generation)** system designed to query a smart home environment using **Neo4j (Graph DB)**, **LanceDB (Vector DB)**, and **NVIDIA NIM (LLM & Embeddings)**. 

Built on the **Agno** framework and wrapped in **AgentOS**, this system allows users to ask natural language questions and get **accurate, grounded, and verified responses** based on real device data, network topologies, and semantic capabilities.

---

## 🚀 Key Features

- ✅ **Natural Language Query API**: High-performance FastAPI endpoint.
- ✅ **Dual-RAG Knowledge Base**: Deterministic graph traversal (Neo4j) + Semantic fuzzy search (LanceDB).
- ✅ **Multi-Step Reasoning Engine**: A rigorous `Think → Action → Analyze` loop preventing hallucinations.
- ✅ **Built-in Shield Guardrails**: Strong protection against prompt injections and destructive Cypher queries.
- ✅ **Agentic Memory & Session Management**: SQLite-backed user preference persistence and multi-turn context.
- ✅ **Full Observability**: OpenTelemetry tracing, token metrics, and latency logs via AgentOS.

---

## 🧠 System Architecture Breakdown

Our architecture is strictly segmented to ensure security, reliability, and precision:

### 1️⃣ Zone 1: Client & State (Left)
- **User Query**: Incoming requests via FastAPI.
- **Multi-Tenant Session Manager**: Handles UUIDs and isolates user sessions.
- **Agentic Memory (SQLite)**: Stores long-term user preferences and conversation history without caching volatile device states.
- *Flow*: User hits FastAPI, session is retrieved from SQLite, and both feed into the Guardrail layer.

### 2️⃣ Zone 2: The Shield (Middle-Left)
- **Input & Execution Guardrails**: The primary defense mechanism.
- **Prompt Injection Guardrail**: Blocks malicious user prompts (e.g., "ignore previous instructions").
- **Cypher Write Guardrail**: A custom regex-based hook that completely blocks `DROP`, `DELETE`, `CREATE`, and `;` injections.
- *Flow*: All inputs pass through here before hitting the Agent. All tool calls pass through here before hitting the database.

### 3️⃣ Zone 3: The Agentic Engine (Center)
- **ReAct Verification Loop**: Powered by NVIDIA NIM (`meta/llama-3.3-70b-instruct`).
- **Think()**: Plans the execution (Cypher vs. Semantic Search).
- **Action()**: Executes tools (schema retrieval, Cypher execution, vector search).
- **Analyze()**: Verifies retrieved data against the user's intent.
- *Flow*: An arrow loops these three steps together (Max 3 Loops) to ensure data sufficiency before answering.

### 4️⃣ Zone 4: Hybrid RAG Execution (Right)
- **Dual-RAG Knowledge Base**:
  - **Deterministic Topology (Neo4j Graph)**: Handles exact paths, triggers, locations, and live state queries.
  - **Semantic Fuzzy Search (LanceDB)**: Handles capability descriptions via BM25 + Vector embeddings.
- *Flow*: The `Action()` step reaches into either Neo4j or LanceDB, passing strictly through the Cypher Guardrail.

### 🏗 The Foundation (Bottom)
- **AgentOS Observability Plane**: A continuous monitoring layer wrapping the entire architecture.
- Integrates **OpenTelemetry**, Token Metrics, and Tool Latency Tracing, ensuring every step, thought, and DB call is logged and auditable.

---

## 🛠 Tech Stack

- **Agent Framework**: Agno + AgentOS
- **Backend API**: FastAPI + Uvicorn
- **LLM**: NVIDIA NIM (`meta/llama-3.3-70b-instruct`)
- **Embeddings**: NVIDIA NIM (`nvidia/llama-nemotron-embed-1b-v2`)
- **Graph Database**: Neo4j (Aura Cloud)
- **Vector Database**: LanceDB (Hybrid Search enabled via Tantivy)
- **State & Memory**: SQLite (`smarthome_sessions`)
- **Telemetry**: OpenTelemetry API/SDK

---

## 📁 Project Structure

```text
/
├── app.py                  # FastAPI entry point & AgentOS wrapper
├── agent.py                # Core Agentic Engine, Tools, Guardrails & config
├── knowledge_builder.py    # LanceDB vector indexing pipeline
├── populate_graph.py       # Neo4j database topology seeder
├── requirements.txt        # Dependencies
├── .env                    # Environment configuration
└── README.md               # Documentation
```

---

## ⚙️ Setup Instructions

### 🔹 1. Clone & Install Dependencies
```bash
git clone <your-repo-url>
cd smart-home-agent
pip install -r requirements.txt
```

### 🔹 2. Configure Environment (`.env`)
Ensure you have a Neo4j instance running (e.g., Neo4j Aura) and an NVIDIA API key.
```env
NVIDIA_API_KEY=your_nvidia_nim_key
NEO4J_URI=neo4j+ssc://your-aura-instance.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
SQLITE_DB_FILE=tmp/smarthome.db
LANCEDB_URI=tmp/lancedb
```

### 🔹 3. Seed the Dual-RAG Knowledge Base
First, populate the Neo4j graph with devices and relationships:
```bash
python populate_graph.py
```
Next, build the LanceDB vector store for semantic capabilities:
```bash
python knowledge_builder.py
```

### 🔹 4. Start the Application
Launch the FastAPI server and AgentOS observability overlay:
```bash
python app.py
```

---

## 🧪 API Usage

Once running, access the interactive API docs at:  
👉 **http://localhost:7777/docs**

### 🔹 Example Query Endpoint (`POST /query`)

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
  "reasoning_trace": [
    "Step 1: [think] Plan Cypher query to find triggers...",
    "Step 2: [get_schema] Fetching node labels and relationships...",
    "Step 3: [run_cypher_query] MATCH (s:Sensor)-[:TRIGGERS]->(l:Light) ...",
    "Step 4: [analyze] Verified sufficient data retrieved."
  ],
  "retrieved_context": [
    "{'s': {'name': 'Hallway Motion Sensor'}, 'l': {'name': 'Hallway Lights'}}"
  ],
  "confidence_score": 0.9,
  "session_id": "51ed0653-0001-47de-b714-a2700ea84db9",
  "elapsed_ms": 1420.5
}
```

---

## 🛡 Security & Hallucination Prevention

The system enforces highly reliable outputs utilizing a multi-layered defense:
1. **Instruction Layer**: Strict prompt directions and routing rules.
2. **Hook Layer (The Shield)**: Custom `CypherWriteGuardrail` preventing write/delete commands before execution, and `PromptInjectionGuardrail`.
3. **Verification Layer**: Self-correction via the ReAct `analyze` loop (Max 3 loops).
4. **Data Layer (Recommended)**: Read-only Neo4j database credentials in production to ensure true immutability.
