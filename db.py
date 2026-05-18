import os
from pymongo import MongoClient

_client = None

def get_db():
    global _client
    if _client is None:
        _client = MongoClient(os.getenv("MONGODB_URI", "mongodb://mongo:27017"))
    return _client["bi_wikolabs"]
