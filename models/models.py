"""
models/models.py
----------------
Data classes and constants for the application.

Project types: BESS / PV String / PV Central
Each type has its own container types and default assignment logic.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Project:
    """Represents a PV/BESS project."""
    name: str
    num_zones: int
    num_blocks: int
    num_containers: int
    description: str = ""
    project_type: str = "BESS"   # BESS / PV String / PV Central
    id: Optional[int] = None
    created_at: Optional[str] = None


@dataclass
class Container:
    """Represents a single physical unit in a project."""
    project_id: int
    zone_number: int
    block_number: int
    container_index: int
    container_type: str
    serial_number: str = ""
    id: Optional[int] = None

    def display_name(self) -> str:
        return f"B{self.block_number} / C{self.container_index} ({self.container_type})"


@dataclass
class LogEntry:
    """Represents one daily maintenance activity."""
    project_id: int
    container_id: int
    date: str
    material_number: str
    quantity: float
    comment: str = ""
    warehouse_id: Optional[int] = None   # warehouse the material was consumed from
    id: Optional[int] = None
    created_at: Optional[str] = None


# ── Project types ─────────────────────────────────────────────────────────────

PROJECT_TYPES = ["BESS", "PV String", "PV Central"]

PROJECT_TYPE_ICONS = {
    "BESS":       "🔋",
    "PV String":  "☀️",
    "PV Central": "⚡",
}

# ── Container types by project type ───────────────────────────────────────────

# All container types across all project types
CONTAINER_TYPES = [
    # BESS
    "Battery",
    "PCS / Converter",
    "LC Cabinet",
    "SCU",
    "MVS",
    "Transformer",
    "RMU",
    "Auxiliary",
    # PV String
    "String Inverter",
    "Logger",
    # PV Central
    "Master Unit",
    "Slave Unit",
    "Combiner Box",
    "SCU Master",
    "SCU Slave",
    # Generic
    "Other",
]

CONTAINER_TYPES_BY_PROJECT = {
    "BESS": [
        "Battery", "PCS / Converter", "LC Cabinet",
        "SCU", "MVS", "Transformer", "RMU", "Auxiliary", "Other",
    ],
    "PV String": [
        "Transformer", "RMU", "Logger", "MVS", "String Inverter", "Other",
    ],
    "PV Central": [
        "Master Unit", "Slave Unit", "Combiner Box",
        "Transformer", "RMU", "SCU Master", "SCU Slave", "Other",
    ],
}


# ── Default container type per project type ───────────────────────────────────

def default_container_type(container_index: int,
                            project_type: str = "BESS") -> str:
    """
    Returns smart default type based on position and project type.

    BESS:
      1 → LC Cabinet, 2 → PCS / Converter, 3+ → Battery

    PV String:
      1 → MVS, 2+ → String Inverter

    PV Central:
      Handled separately in the wizard (Master/Slave/Combiner split)
      Default fallback: Master Unit
    """
    if project_type == "BESS":
        if container_index == 1:   return "LC Cabinet"
        elif container_index == 2: return "PCS / Converter"
        else:                      return "Battery"

    elif project_type == "PV String":
        if container_index == 1:   return "MVS"
        else:                      return "String Inverter"

    elif project_type == "PV Central":
        return "Master Unit"

    return "Other"
