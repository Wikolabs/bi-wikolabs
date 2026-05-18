"""Step 2: Entity Resolution — fuzzy-match entity names against MongoDB collections."""

import re
from typing import Any


async def resolve(entities: list, db) -> dict:
    """Resolve extracted entities to their DB records.

    Args:
        entities: list of dicts with keys 'type' and 'value'
        db: pymongo Database instance

    Returns:
        dict mapping entity value (original string) → resolved info dict
    """
    entity_map: dict[str, Any] = {}

    for entity in entities:
        etype = entity.get("type", "").lower()
        evalue = entity.get("value", "").strip()

        if not evalue:
            continue

        if etype == "region":
            # No DB lookup for regions — pass through as-is
            entity_map[evalue] = {"type": "region", "value": evalue}
            continue

        collection_name, id_field, name_field = _collection_for_type(etype)
        if collection_name is None:
            entity_map[evalue] = {"type": etype, "value": evalue}
            continue

        # Case-insensitive regex search on the name field
        pattern = re.compile(re.escape(evalue), re.IGNORECASE)
        doc = db[collection_name].find_one(
            {name_field: {"$regex": pattern}},
            {id_field: 1, name_field: 1, "_id": 0},
        )

        if doc:
            entity_map[evalue] = {
                "type": etype,
                "name": doc.get(name_field, evalue),
                "id": doc.get(id_field),
                "resolved": True,
            }
        else:
            # Not found — store the original value so MQL generator can still try
            entity_map[evalue] = {
                "type": etype,
                "value": evalue,
                "resolved": False,
            }

    return entity_map


def _collection_for_type(etype: str):
    """Return (collection, id_field, name_field) for a given entity type."""
    mapping = {
        "customer": ("customers", "customer_id", "name"),
        "product": ("products", "product_id", "name"),
        "employee": ("employees", "employee_id", "name"),
    }
    info = mapping.get(etype)
    if info:
        return info
    return None, None, None
