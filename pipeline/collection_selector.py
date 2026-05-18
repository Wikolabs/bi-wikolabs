"""Step 3: Collection Selection — rule-based mapping from intent to collections."""

# All available collections
ALL_COLLECTIONS = ["sales", "products", "customers", "employees", "transactions"]

# Keyword → collection mappings (checked against intent + question tokens)
_KEYWORD_MAP = {
    "sales": [
        "sale", "sales", "order", "orders", "revenue", "selling", "sold",
        "deal", "deals", "purchase", "purchases", "invoice",
    ],
    "products": [
        "product", "products", "item", "items", "sku", "catalog", "inventory",
        "stock", "category", "categories", "margin", "margins", "supplier",
        "cost", "price", "pricing",
    ],
    "customers": [
        "customer", "customers", "client", "clients", "segment", "segments",
        "enterprise", "smb", "individual", "buyer", "buyers", "account",
        "accounts",
    ],
    "employees": [
        "employee", "employees", "staff", "headcount", "payroll", "salary",
        "salaries", "department", "departments", "hr", "hire", "performance",
        "manager", "managers", "role", "roles",
    ],
    "transactions": [
        "transaction", "transactions", "cash", "flow", "expense", "expenses",
        "finance", "financial", "payment", "payments", "refund", "refunds",
        "income", "cost", "costs", "budget",
    ],
}

# query_type → likely collections (used as a tiebreaker / fallback)
_TYPE_DEFAULTS = {
    "metric": ["sales", "transactions"],
    "ranking": ["sales", "products"],
    "comparison": ["sales", "customers"],
    "list": ["sales"],
    "lookup": ["customers", "products", "employees"],
    "smalltalk": [],
}

# Entity type → collection that needs to be included for joins
_ENTITY_COLLECTION = {
    "customer": "customers",
    "product": "products",
    "employee": "employees",
}


def select(intent: str, query_type: str, entities: list) -> list:
    """Return a list of 1-3 collection names relevant to the query.

    Args:
        intent: short description from the analyzer
        query_type: one of smalltalk|lookup|list|metric|ranking|comparison
        entities: list of entity dicts from the analyzer

    Returns:
        Ordered list of collection names (primary first)
    """
    if query_type == "smalltalk":
        return []

    tokens = _tokenize(intent)
    scored: dict[str, int] = {col: 0 for col in ALL_COLLECTIONS}

    # Score by keyword matching
    for col, keywords in _KEYWORD_MAP.items():
        for kw in keywords:
            if kw in tokens:
                scored[col] += 1

    # Boost collections implied by entity types
    for entity in entities:
        etype = entity.get("type", "").lower()
        implied = _ENTITY_COLLECTION.get(etype)
        if implied:
            scored[implied] += 2

    # Apply query_type defaults as a weak prior (score +1 for each default)
    for col in _TYPE_DEFAULTS.get(query_type, []):
        scored[col] += 1

    # Keep collections with score > 0, sorted descending
    selected = [col for col, s in sorted(scored.items(), key=lambda x: -x[1]) if s > 0]

    if not selected:
        # Absolute fallback
        selected = _TYPE_DEFAULTS.get(query_type, ["sales"])

    # Limit to top 3 to keep prompts focused
    return selected[:3]


def _tokenize(text: str) -> set:
    """Lowercase word tokens from a string."""
    import re
    return set(re.findall(r"[a-z]+", text.lower()))
