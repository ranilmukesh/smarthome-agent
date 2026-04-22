"""
populate_graph.py
-----------------
Seeds the Neo4j knowledge graph with smart-home device nodes (17) and
relationships (25+).  Run once before starting the API:

    python populate_graph.py
"""

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# ---------------------------------------------------------------------------
# Device data  (device_id, name, device_type, location, state, manufacturer)
# ---------------------------------------------------------------------------
DEVICES = [
    ("thermo_lr",  "Smart Thermostat",     "controller", "Living Room",  "72°F",      "Nest"),
    ("light_br",   "Smart Light Bulb",     "actuator",   "Bedroom",      "off",       "Philips"),
    ("light_kt",   "Smart Light Bulb",     "actuator",   "Kitchen",      "on",        "Philips"),
    ("light_lr",   "Smart Light Bulb",     "actuator",   "Living Room",  "off",       "Philips"),
    ("motion_hw",  "Motion Sensor",        "sensor",     "Hallway",      "idle",      "Aqara"),
    ("motion_gr",  "Motion Sensor",        "sensor",     "Garage",       "idle",      "Aqara"),
    ("lock_fd",    "Smart Door Lock",      "actuator",   "Front Door",   "locked",    "Schlage"),
    ("cam_fd",     "Security Camera",      "sensor",     "Front Door",   "recording", "Ring"),
    ("cam_by",     "Security Camera",      "sensor",     "Backyard",     "idle",      "Ring"),
    ("speaker_kt", "Smart Speaker",        "controller", "Kitchen",      "on",        "Amazon"),
    ("temp_br",    "Temperature Sensor",   "sensor",     "Bedroom",      "68°F",      "Sensirion"),
    ("temp_lr",    "Temperature Sensor",   "sensor",     "Living Room",  "72°F",      "Sensirion"),
    ("plug_br",    "Smart Plug",           "actuator",   "Bedroom",      "off",       "TP-Link"),
    ("plug_kt",    "Smart Plug",           "actuator",   "Kitchen",      "on",        "TP-Link"),
    ("hum_bt",     "Humidity Sensor",      "sensor",     "Bathroom",     "55%",       "Sensirion"),
    ("win_br",     "Window Sensor",        "sensor",     "Bedroom",      "closed",    "Aqara"),
    ("win_lr",     "Window Sensor",        "sensor",     "Living Room",  "closed",    "Aqara"),
]

# ---------------------------------------------------------------------------
# Relationships  (source_id, relationship_type, target_id)
# ---------------------------------------------------------------------------
RELATIONSHIPS = [
    # Automation triggers
    ("motion_hw", "TRIGGERS",       "light_lr"),
    ("motion_gr", "TRIGGERS",       "light_kt"),
    # Data feeds
    ("temp_lr",   "FEEDS_DATA_TO",  "thermo_lr"),
    ("temp_br",   "FEEDS_DATA_TO",  "thermo_lr"),
    ("hum_bt",    "FEEDS_DATA_TO",  "thermo_lr"),
    # Control
    ("speaker_kt","CONTROLS",       "light_kt"),
    ("speaker_kt","CONTROLS",       "light_lr"),
    ("speaker_kt","CONTROLS",       "plug_kt"),
    # Security
    ("cam_fd",    "MONITORS",       "lock_fd"),
    ("cam_by",    "MONITORS",       "cam_by"),       # self-monitor backyard zone
    ("lock_fd",   "SECURES",        "cam_fd"),
    # Power
    ("plug_kt",   "POWERS",         "speaker_kt"),
    ("plug_br",   "POWERS",         "light_br"),
    # Regulation
    ("thermo_lr", "REGULATES",      "temp_lr"),
    ("thermo_lr", "REGULATES",      "temp_br"),
    # Window/environment
    ("win_br",    "REPORTS_TO",     "thermo_lr"),
    ("win_lr",    "REPORTS_TO",     "thermo_lr"),
    # Location / zone relationships
    ("motion_hw", "LOCATED_IN",     "light_hw"),  # conceptual – see note
    ("cam_fd",    "LOCATED_AT",     "lock_fd"),
    ("temp_br",   "LOCATED_IN",     "light_br"),
    # Cross-sensor alert chains
    ("motion_gr", "ALERTS",         "cam_by"),
    ("cam_fd",    "ALERTS",         "lock_fd"),
    # Smart-speaker voice control chain
    ("speaker_kt","CONTROLS",       "thermo_lr"),
    # Plug → light (bedroom)
    ("plug_br",   "POWERS",         "temp_br"),
    # Window open → thermostat adjusts
    ("win_br",    "TRIGGERS",       "thermo_lr"),
]

# NOTE: "light_hw" doesn't exist; we'll skip invalid targets gracefully.


def _create_indexes(tx) -> None:
    """Create property and full-text indexes for performance."""
    tx.run("CREATE INDEX device_id_idx IF NOT EXISTS FOR (d:Device) ON (d.device_id)")
    tx.run("CREATE INDEX device_loc_idx IF NOT EXISTS FOR (d:Device) ON (d.location)")
    tx.run("CREATE INDEX device_type_idx IF NOT EXISTS FOR (d:Device) ON (d.device_type)")
    tx.run("CREATE INDEX device_state_idx IF NOT EXISTS FOR (d:Device) ON (d.state)")


def _device_description(name: str, dtype: str, location: str, state: str, mfr: str) -> str:
    """Plain-text description embedded in the node for vector search."""
    return (
        f"{name} is a {dtype} made by {mfr}, located in the {location}. "
        f"Its current state is {state}."
    )


def seed(driver) -> None:
    ts = datetime.now(timezone.utc).isoformat()

    with driver.session(database="9d77d39a") as s:
        print("Clearing existing data …")
        s.run("MATCH (n) DETACH DELETE n")

        print("Creating indexes …")
        s.execute_write(_create_indexes)

        print(f"Inserting {len(DEVICES)} device nodes …")
        for did, name, dtype, loc, state, mfr in DEVICES:
            description = _device_description(name, dtype, loc, state, mfr)
            s.run(
                """
                CREATE (d:Device {
                    device_id:   $device_id,
                    name:        $name,
                    device_type: $device_type,
                    location:    $location,
                    state:       $state,
                    manufacturer:$manufacturer,
                    description: $description,
                    created_at:  $created_at
                })
                """,
                device_id=did, name=name, device_type=dtype, location=loc,
                state=state, manufacturer=mfr, description=description,
                created_at=ts,
            )

        print(f"Inserting {len(RELATIONSHIPS)} relationships …")
        skipped = 0
        for src, rel, tgt in RELATIONSHIPS:
            # Skip if target device ID doesn't exist (graceful)
            result = s.run(
                f"""
                MATCH (x:Device {{device_id: $src}})
                MATCH (y:Device {{device_id: $tgt}})
                CREATE (x)-[:{rel} {{
                    relationship_type: $rel,
                    strength:          1.0,
                    created_at:        $ts
                }}]->(y)
                RETURN count(*) AS created
                """,
                src=src, tgt=tgt, rel=rel, ts=ts,
            )
            record = result.single()
            if record is None or record["created"] == 0:
                print(f"  WARNING: Skipped ({src})-[{rel}]->({tgt}) — node not found")
                skipped += 1

        total = len(RELATIONSHIPS) - skipped
        print(f"\nSUCCESS: Seeded {len(DEVICES)} devices and {total} relationships. ({skipped} skipped)")

        # Quick verification
        count = s.run("MATCH (d:Device) RETURN count(d) AS n").single()["n"]
        rel_count = s.run("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"]



if __name__ == "__main__":
    print(f"Connecting to Neo4j at {NEO4J_URI} …")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
        driver.verify_connectivity()
    except Exception as e:
        print(f"FAILED: Cannot connect to Neo4j: {e}")
        sys.exit(1)

    try:
        seed(driver)
    finally:
        driver.close()
