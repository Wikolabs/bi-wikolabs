"""Dynamic MongoDB schema extractor — samples collections and builds LLM-readable schemas."""

from bson import ObjectId
from datetime import datetime
from typing import Optional


class SchemaExtractor:
    def __init__(self, db, sample_size: int = 20):
        self.db = db
        self.sample_size = sample_size
        self._cache: dict = {}

    def get_collections(self) -> list[str]:
        return sorted(c for c in self.db.list_collection_names() if not c.startswith("system."))

    def extract(self, collection: str, force: bool = False) -> dict:
        if collection in self._cache and not force:
            return self._cache[collection]

        docs = list(self.db[collection].aggregate([{"$sample": {"size": self.sample_size}}]))
        total = self.db[collection].count_documents({})

        field_info: dict = {}
        for doc in docs:
            for key, val in doc.items():
                if key not in field_info:
                    field_info[key] = {
                        "types":      set(),
                        "examples":   [],
                        "is_oid_ref": False,
                        "enum_vals":  set(),
                    }
                fi = field_info[key]
                t = self._type_name(val)
                fi["types"].add(t)
                if t == "ObjectId" and key != "_id":
                    fi["is_oid_ref"] = True
                if val is not None and len(fi["examples"]) < 3:
                    fi["examples"].append(self._safe_str(val))
                if t == "str" and val and len(fi["enum_vals"]) < 12:
                    fi["enum_vals"].add(val)

        fields = {}
        for key, fi in field_info.items():
            dominant = self._dominant_type(fi["types"])
            entry: dict = {
                "type":       dominant,
                "examples":   fi["examples"],
                "is_oid_ref": fi["is_oid_ref"],
            }
            # Mark low-cardinality string fields as enums
            if dominant == "str" and 1 < len(fi["enum_vals"]) <= 10:
                entry["enum"] = sorted(fi["enum_vals"])
            fields[key] = entry

        schema = {"collection": collection, "total_docs": total, "fields": fields}
        self._cache[collection] = schema
        return schema

    def to_prompt(self, collection: str) -> str:
        """Return compact schema string for LLM consumption."""
        s = self.extract(collection)
        lines = [f"Collection `{collection}` ({s['total_docs']:,} documents):"]
        for field, info in s["fields"].items():
            if field == "_id":
                continue
            ref = " [→ObjectId ref]" if info["is_oid_ref"] else ""
            if "enum" in info:
                vals = " | ".join(f'"{v}"' for v in info["enum"])
                lines.append(f"  {field}: {info['type']}{ref}  values: {vals}")
            else:
                ex = " | ".join(str(e) for e in info["examples"][:2])
                lines.append(f"  {field}: {info['type']}{ref}  e.g. {ex}")
        return "\n".join(lines)

    def to_summary_line(self, collection: str) -> str:
        """One-line summary for collection selection prompts."""
        s = self.extract(collection)
        fields = [k for k in s["fields"] if k != "_id"][:10]
        return f"`{collection}` ({s['total_docs']:,} docs) — {', '.join(fields)}"

    def all_prompts(self) -> dict[str, str]:
        return {c: self.to_prompt(c) for c in self.get_collections()}

    def all_summaries(self) -> dict[str, str]:
        return {c: self.to_summary_line(c) for c in self.get_collections()}

    def invalidate(self, collection: Optional[str] = None):
        if collection:
            self._cache.pop(collection, None)
        else:
            self._cache.clear()

    @staticmethod
    def _type_name(val) -> str:
        if val is None:              return "null"
        if isinstance(val, ObjectId): return "ObjectId"
        if isinstance(val, bool):    return "bool"
        if isinstance(val, int):     return "int"
        if isinstance(val, float):   return "float"
        if isinstance(val, str):     return "str"
        if isinstance(val, datetime): return "date"
        if isinstance(val, list):    return "array"
        if isinstance(val, dict):    return "object"
        return type(val).__name__

    @staticmethod
    def _dominant_type(types: set) -> str:
        for t in ["str", "ObjectId", "date", "float", "int", "bool", "array", "object"]:
            if t in types:
                return t
        return next(iter(types), "unknown")

    @staticmethod
    def _safe_str(val) -> str:
        if isinstance(val, ObjectId):  return "ObjectId"
        if isinstance(val, datetime):  return val.strftime("%Y-%m-%d")
        if isinstance(val, list):      return f"[{len(val)} items]"
        return str(val)[:40]
