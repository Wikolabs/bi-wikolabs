"""Seed BI Wikolabs with realistic enterprise demo data."""

import os
import sys
import random
from datetime import datetime, timedelta
from pymongo import MongoClient
from faker import Faker

fake = Faker()
random.seed(42)
Faker.seed(42)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
client = MongoClient(MONGODB_URI)
db = client["bi_wikolabs"]

REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East"]
SEGMENTS = ["Enterprise", "SMB", "Individual"]
DEPARTMENTS = ["Sales", "Engineering", "Finance", "HR", "Operations"]
DEPT_ROLES = {
    "Sales": ["Account Executive", "Sales Manager", "SDR", "VP of Sales"],
    "Engineering": ["Software Engineer", "Senior Engineer", "Tech Lead", "CTO"],
    "Finance": ["Accountant", "Financial Analyst", "Controller", "CFO"],
    "HR": ["HR Specialist", "Recruiter", "HR Manager", "CHRO"],
    "Operations": ["Operations Analyst", "Operations Manager", "COO", "Coordinator"],
}
DEPT_SALARY = {
    "Sales": (45_000, 120_000),
    "Engineering": (65_000, 150_000),
    "Finance": (55_000, 130_000),
    "HR": (40_000, 90_000),
    "Operations": (42_000, 95_000),
}
CATALOG = [
    ("Laptop Pro 15", "Electronics", 1299.99, 780),
    ("Wireless Mouse", "Electronics", 49.99, 18),
    ("4K Monitor 27\"", "Electronics", 599.99, 340),
    ("Mechanical Keyboard", "Electronics", 149.99, 68),
    ("USB-C Docking Station", "Electronics", 129.99, 55),
    ("Noise-Cancelling Headset", "Electronics", 249.99, 120),
    ("Standing Desk (Electric)", "Furniture", 899.99, 520),
    ("Ergonomic Chair", "Furniture", 699.99, 380),
    ("3-Drawer Filing Cabinet", "Furniture", 299.99, 140),
    ("Glass Whiteboard 180cm", "Furniture", 399.99, 200),
    ("Conference Table 10-seat", "Furniture", 1499.99, 850),
    ("A4 Paper Ream (500 sheets)", "Office Supplies", 12.99, 4),
    ("Ballpoint Pens Box (50)", "Office Supplies", 9.99, 3),
    ("Heavy-Duty Stapler", "Office Supplies", 24.99, 8),
    ("Sticky Notes Pack (12)", "Office Supplies", 7.99, 2),
    ("Printer Ink Cartridge Set", "Office Supplies", 54.99, 22),
    ("CRM Suite (annual license)", "Software", 2400.00, 1100),
    ("Project Management Tool", "Software", 1200.00, 550),
    ("Analytics Platform", "Software", 3600.00, 1700),
    ("HR Management System", "Software", 1800.00, 830),
    ("Cloud Storage 1TB/yr", "Software", 120.00, 40),
    ("Cybersecurity Suite", "Software", 2800.00, 1300),
    ("IT Consulting (per day)", "Services", 1500.00, 800),
    ("Employee Training Workshop", "Services", 2500.00, 1200),
    ("System Integration Project", "Services", 5000.00, 3000),
    ("Data Migration Service", "Services", 3500.00, 2000),
    ("Annual Support Contract", "Services", 4800.00, 2400),
]
SUPPLIERS = ["TechCorp Ltd", "OfficePlus", "FurniturePro", "SoftwareDirect", "ConsultGroup"]


def rdate(days_ago_start=730, days_ago_end=0):
    start = datetime.utcnow() - timedelta(days=days_ago_start)
    end = datetime.utcnow() - timedelta(days=days_ago_end)
    delta = (end - start).total_seconds()
    return start + timedelta(seconds=random.random() * delta)


def seed_products():
    db.products.drop()
    products = []
    for i, (name, cat, price, cost) in enumerate(CATALOG):
        products.append({
            "product_id": f"PRD{i+1:03d}",
            "name": name,
            "category": cat,
            "unit_price": price,
            "cost_price": cost,
            "stock_quantity": random.randint(10, 500),
            "supplier": random.choice(SUPPLIERS),
            "sku": f"SKU-{cat[:3].upper()}-{i+1:04d}",
            "is_active": True,
        })
    db.products.insert_many(products)
    print(f"  ✓ {len(products)} products")
    return products


def seed_customers():
    db.customers.drop()
    customers = []
    for i in range(120):
        seg = random.choices(SEGMENTS, weights=[20, 45, 35])[0]
        reg_date = rdate(1200, 30)
        n_orders = random.randint(1, 60)
        customers.append({
            "customer_id": f"CUS{i+1:04d}",
            "name": fake.company() if seg != "Individual" else fake.name(),
            "email": fake.email(),
            "segment": seg,
            "region": random.choice(REGIONS),
            "country": fake.country(),
            "registration_date": reg_date,
            "total_orders": n_orders,
            "total_spent": round(n_orders * random.uniform(150, 6000), 2),
            "is_active": random.random() > 0.08,
        })
    db.customers.insert_many(customers)
    print(f"  ✓ {len(customers)} customers")
    return customers


def seed_employees():
    db.employees.drop()
    employees = []
    for i in range(40):
        dept = random.choice(DEPARTMENTS)
        lo, hi = DEPT_SALARY[dept]
        employees.append({
            "employee_id": f"EMP{i+1:03d}",
            "name": fake.name(),
            "department": dept,
            "role": random.choice(DEPT_ROLES[dept]),
            "salary": round(random.uniform(lo, hi), 2),
            "hire_date": rdate(2500, 30),
            "manager_id": f"EMP{random.randint(1, 5):03d}" if i >= 5 else None,
            "performance_score": round(random.uniform(2.5, 5.0), 1),
            "is_active": random.random() > 0.05,
        })
    db.employees.insert_many(employees)
    print(f"  ✓ {len(employees)} employees")
    return employees


def seed_sales(products, customers):
    db.sales.drop()
    sales = []
    reps = [fake.name() for _ in range(10)]
    for i in range(600):
        p = random.choice(products)
        c = random.choice(customers)
        qty = random.randint(1, 20)
        disc = random.choices([0, 0.05, 0.10, 0.15, 0.20], weights=[50, 20, 15, 10, 5])[0]
        total = round(qty * p["unit_price"] * (1 - disc), 2)
        o_date = rdate(730, 0)
        sales.append({
            "order_id": f"ORD{i+1:05d}",
            "customer_id": c["customer_id"],
            "product_id": p["product_id"],
            "quantity": qty,
            "unit_price": p["unit_price"],
            "total_amount": total,
            "discount": disc,
            "region": c["region"],
            "sales_rep": random.choice(reps),
            "status": random.choices(["completed", "pending", "cancelled"], weights=[75, 15, 10])[0],
            "order_date": o_date,
            "delivery_date": o_date + timedelta(days=random.randint(1, 14)),
        })
    db.sales.insert_many(sales)
    print(f"  ✓ {len(sales)} sales orders")


def seed_transactions():
    db.transactions.drop()
    transactions = []
    type_categories = {
        "revenue": ["Sales", "Services"],
        "expense": ["Marketing", "Operations", "Payroll"],
        "refund": ["Refund"],
    }
    for i in range(400):
        t = random.choices(["revenue", "expense", "refund"], weights=[55, 35, 10])[0]
        transactions.append({
            "transaction_id": f"TXN{i+1:05d}",
            "type": t,
            "amount": round(random.uniform(100, 50000), 2),
            "currency": "USD",
            "date": rdate(730, 0),
            "category": random.choice(type_categories[t]),
            "status": random.choices(["completed", "pending", "failed"], weights=[85, 10, 5])[0],
            "notes": fake.sentence(),
        })
    db.transactions.insert_many(transactions)
    print(f"  ✓ {len(transactions)} transactions")


def create_indexes():
    db.sales.create_index("order_date")
    db.sales.create_index("customer_id")
    db.sales.create_index("product_id")
    db.sales.create_index("region")
    db.customers.create_index("segment")
    db.transactions.create_index("date")
    print("  ✓ indexes created")


if __name__ == "__main__":
    if db.sales.count_documents({}) > 0:
        print("Database already seeded — skipping.")
        sys.exit(0)

    print("Seeding BI Wikolabs demo data...")
    prods = seed_products()
    custs = seed_customers()
    seed_employees()
    seed_sales(prods, custs)
    seed_transactions()
    create_indexes()
    print("✅ Done!")
