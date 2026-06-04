"""Step 2: Entity Resolution · resolve entity names to MongoDB _id ObjectIds."""

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
        etype  = entity.get("type", "").lower()
        evalue = (entity.get("value") or "").strip()
        if not evalue:
            continue
        if etype == "region":
            entity_map[evalue] = {"type": "region", "value": evalue, "resolved": False}
            continue

        result = await _resolve_entity(etype, evalue, db)
        entity_map[evalue] = result
    return entity_map


async def _resolve_entity(etype: str, value: str, db) -> dict:
    pattern = re.compile(re.escape(value), re.IGNORECASE)

    # person → search contacts first, then employees
    if etype == "person":
        result = _search_collection(db, "contacts",  pattern,
                                    extra_fields=["title", "company_id", "company_name"])
        if result:
            result["type"] = "contact"
            return result
        result = _search_collection(db, "employees", pattern,
                                    extra_fields=["role", "department"])
        if result:
            result["type"] = "employee"
            return result
        return {"type": "person", "value": value, "resolved": False}

    col_map = {
        "customer": ("customers", ["industry", "segment", "region"]),
        "contact":  ("contacts",  ["title", "company_id", "company_name"]),
        "product":  ("products",  ["category", "unit_price"]),
        "employee": ("employees", ["role", "department", "quota", "quota_attainment"]),
        "supplier": ("suppliers", ["country"]),
    }

    if etype not in col_map:
        return {"type": etype, "value": value, "resolved": False}

    collection, extra_fields = col_map[etype]
    result = _search_collection(db, collection, pattern, extra_fields=extra_fields)
    if result:
        result["type"] = etype
        return result

    return {"type": etype, "value": value, "resolved": False}


def _search_collection(db, collection: str, pattern,
                        extra_fields: list = None) -> dict | None:
    projection = {"_id": 1, "name": 1}
    if extra_fields:
        for f in extra_fields:
            projection[f] = 1

    doc = db[collection].find_one({"name": {"$regex": pattern}}, projection)
    if not doc:
        return None

    result = {
        "name":     doc.get("name", ""),
        "_id":      str(doc["_id"]),
        "resolved": True,
    }
    if extra_fields:
        for f in extra_fields:
            if f in doc:
                val = doc[f]
                # ObjectId fields → stringify so JSON-serializable
                result[f] = str(val) if hasattr(val, "generation_time") else val
    return result
