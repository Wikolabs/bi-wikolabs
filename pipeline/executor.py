"""Step 5: Execute & Validate with retry — run MQL and self-correct on failure."""

from pipeline.mql_generator import generate

MAX_RETRIES = 3


async def execute_with_retry(
    spec: dict,
    question: str,
    analysis: dict,
    collections: list,
    entity_map: dict,
    db,
) -> list:
    """Execute the MQL spec against MongoDB, retrying up to MAX_RETRIES times.

    On failure, feeds the error back to the MQL generator for self-correction.

    Args:
        spec: MQL specification dict (from mql_generator.generate)
        question: original user question
        analysis: output from analyzer.analyze()
        collections: selected collection names
        entity_map: resolved entities
        db: pymongo Database instance

    Returns:
        list of result documents

    Raises:
        RuntimeError: if all retries are exhausted
    """
    current_spec = spec
    last_error: str | None = None

    for attempt in range(MAX_RETRIES):
        try:
            return _execute(current_spec, db)
        except Exception as exc:
            last_error = str(exc)
            if attempt < MAX_RETRIES - 1:
                # Self-correct: regenerate query with error context
                current_spec = await generate(
                    question=question,
                    analysis=analysis,
                    collections=collections,
                    entity_map=entity_map,
                    error_context=last_error,
                )
            else:
                raise RuntimeError(
                    f"Query failed after {MAX_RETRIES} attempts. Last error: {last_error}"
                ) from exc

    # Should never reach here
    raise RuntimeError(f"Query failed. Last error: {last_error}")


def _execute(spec: dict, db) -> list:
    """Execute a single MQL spec against the database."""
    collection_name = spec.get("collection")
    if not collection_name:
        raise ValueError("MQL spec missing 'collection' field")

    col = db[collection_name]
    operation = spec.get("operation", "find")

    if operation == "aggregate":
        pipeline = spec.get("pipeline")
        if not isinstance(pipeline, list):
            raise ValueError("Aggregation spec missing valid 'pipeline' list")
        return list(col.aggregate(pipeline))

    if operation == "find":
        filt = spec.get("filter", {})
        projection = spec.get("projection", {})
        limit = int(spec.get("limit", 50))
        cursor = col.find(filt, projection)
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)

    raise ValueError(f"Unknown operation '{operation}'. Expected 'aggregate' or 'find'.")
