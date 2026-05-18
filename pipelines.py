"""Seven pre-built aggregation pipelines for demo analytics."""

from db import get_db


def revenue_by_month():
    db = get_db()
    return list(db.sales.aggregate([
        {"$match": {"status": "completed"}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$order_date"}},
            "revenue": {"$sum": "$total_amount"},
            "orders": {"$sum": 1},
            "avg_order_value": {"$avg": "$total_amount"},
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "month": "$_id",
            "revenue": {"$round": ["$revenue", 2]},
            "orders": 1,
            "avg_order_value": {"$round": ["$avg_order_value", 2]},
            "_id": 0,
        }},
    ]))


def top_products_by_revenue(limit: int = 10):
    db = get_db()
    return list(db.sales.aggregate([
        {"$match": {"status": "completed"}},
        {"$group": {
            "_id": "$product_id",
            "revenue": {"$sum": "$total_amount"},
            "units_sold": {"$sum": "$quantity"},
            "orders": {"$sum": 1},
        }},
        {"$sort": {"revenue": -1}},
        {"$limit": limit},
        {"$lookup": {
            "from": "products",
            "localField": "_id",
            "foreignField": "product_id",
            "as": "product",
        }},
        {"$unwind": "$product"},
        {"$project": {
            "product_name": "$product.name",
            "category": "$product.category",
            "revenue": {"$round": ["$revenue", 2]},
            "units_sold": 1,
            "orders": 1,
            "_id": 0,
        }},
    ]))


def customer_segment_analysis():
    db = get_db()
    return list(db.sales.aggregate([
        {"$match": {"status": "completed"}},
        {"$lookup": {
            "from": "customers",
            "localField": "customer_id",
            "foreignField": "customer_id",
            "as": "customer",
        }},
        {"$unwind": "$customer"},
        {"$group": {
            "_id": "$customer.segment",
            "revenue": {"$sum": "$total_amount"},
            "orders": {"$sum": 1},
            "unique_customers": {"$addToSet": "$customer_id"},
            "avg_order_value": {"$avg": "$total_amount"},
        }},
        {"$project": {
            "segment": "$_id",
            "revenue": {"$round": ["$revenue", 2]},
            "orders": 1,
            "unique_customers": {"$size": "$unique_customers"},
            "avg_order_value": {"$round": ["$avg_order_value", 2]},
            "_id": 0,
        }},
        {"$sort": {"revenue": -1}},
    ]))


def regional_performance():
    db = get_db()
    return list(db.sales.aggregate([
        {"$match": {"status": "completed"}},
        {"$group": {
            "_id": "$region",
            "revenue": {"$sum": "$total_amount"},
            "orders": {"$sum": 1},
            "avg_discount_pct": {"$avg": "$discount"},
        }},
        {"$project": {
            "region": "$_id",
            "revenue": {"$round": ["$revenue", 2]},
            "orders": 1,
            "avg_discount_pct": {"$round": [{"$multiply": ["$avg_discount_pct", 100]}, 1]},
            "_id": 0,
        }},
        {"$sort": {"revenue": -1}},
    ]))


def headcount_and_payroll():
    db = get_db()
    return list(db.employees.aggregate([
        {"$match": {"is_active": True}},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "avg_salary": {"$avg": "$salary"},
            "total_payroll": {"$sum": "$salary"},
            "avg_performance": {"$avg": "$performance_score"},
        }},
        {"$project": {
            "department": "$_id",
            "headcount": 1,
            "avg_salary": {"$round": ["$avg_salary", 0]},
            "total_payroll": {"$round": ["$total_payroll", 0]},
            "avg_performance": {"$round": ["$avg_performance", 1]},
            "_id": 0,
        }},
        {"$sort": {"total_payroll": -1}},
    ]))


def cash_flow_by_month():
    db = get_db()
    return list(db.transactions.aggregate([
        {"$match": {"status": "completed"}},
        {"$group": {
            "_id": {
                "month": {"$dateToString": {"format": "%Y-%m", "date": "$date"}},
                "type": "$type",
            },
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id.month": 1, "_id.type": 1}},
        {"$project": {
            "month": "$_id.month",
            "type": "$_id.type",
            "total": {"$round": ["$total", 2]},
            "count": 1,
            "_id": 0,
        }},
    ]))


def product_category_margins():
    db = get_db()
    return list(db.sales.aggregate([
        {"$match": {"status": "completed"}},
        {"$lookup": {
            "from": "products",
            "localField": "product_id",
            "foreignField": "product_id",
            "as": "product",
        }},
        {"$unwind": "$product"},
        {"$group": {
            "_id": "$product.category",
            "revenue": {"$sum": "$total_amount"},
            "cogs": {"$sum": {"$multiply": ["$quantity", "$product.cost_price"]}},
            "units_sold": {"$sum": "$quantity"},
        }},
        {"$project": {
            "category": "$_id",
            "revenue": {"$round": ["$revenue", 0]},
            "cogs": {"$round": ["$cogs", 0]},
            "gross_profit": {"$round": [{"$subtract": ["$revenue", "$cogs"]}, 0]},
            "margin_pct": {"$round": [
                {"$multiply": [
                    {"$divide": [{"$subtract": ["$revenue", "$cogs"]}, "$revenue"]},
                    100,
                ]},
                1,
            ]},
            "units_sold": 1,
            "_id": 0,
        }},
        {"$sort": {"gross_profit": -1}},
    ]))


PIPELINE_MAP = {
    "revenue trend": revenue_by_month,
    "top products": top_products_by_revenue,
    "customer segments": customer_segment_analysis,
    "regional performance": regional_performance,
    "hr dashboard": headcount_and_payroll,
    "cash flow": cash_flow_by_month,
    "product margins": product_category_margins,
}


def match_pipeline(question: str):
    q = question.lower()
    for keyword, fn in PIPELINE_MAP.items():
        if any(w in q for w in keyword.split()):
            return fn
    return None
