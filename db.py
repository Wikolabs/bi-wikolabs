import os
from pymongo import MongoClient

_client = None
_extractor = None


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(os.getenv("MONGODB_URI", "mongodb://mongo:27017"))
    return _client["bi_wikolabs"]


def get_extractor():
    global _extractor
    from schema_extractor import SchemaExtractor
    if _extractor is None:
        _extractor = SchemaExtractor(get_db())
    return _extractor
