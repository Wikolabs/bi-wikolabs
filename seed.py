"""Seed BI Wikolabs with realistic enterprise demo data.

Always drops and reseeds · run unconditionally to get a fresh state.
7 collections: suppliers, products, customers, contacts, employees, orders, transactions.
All cross-references use ObjectId.
"""

import os
import random
from datetime import datetime, timedelta

from bson import ObjectId
from faker import Faker
from pymongo import MongoClient, ASCENDING

fake = Faker()
random.seed(42)
Faker.seed(42)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
client = MongoClient(MONGODB_URI)
db = client["bi_wikolabs"]

# ── Named entities ─────────────────────────────────────────────────────────────

NAMED_SUPPLIERS = [
    {"name": "Dell Technologies",     "country": "United States", "lead_time_days": 7},
    {"name": "Microsoft Corporation", "country": "United States", "lead_time_days": 1},
    {"name": "Herman Miller Group",   "country": "United States", "lead_time_days": 14},
    {"name": "Steelcase Inc",         "country": "United States", "lead_time_days": 21},
    {"name": "Logitech International","country": "Switzerland",   "lead_time_days": 5},
    {"name": "Cisco Systems",         "country": "United States", "lead_time_days": 10},
    {"name": "SAP SE",                "country": "Germany",       "lead_time_days": 3},
    {"name": "Accenture PLC",         "country": "Ireland",       "lead_time_days": 1},
]

# (name, category, unit_price, cost_price, supplier_index)
CATALOG = [
    # Electronics → Dell (0), Logitech (4), Cisco (5)
    ("Laptop Pro 15",              "Electronics",    1299.99,  780.00, 0),
    ("Wireless Mouse",             "Electronics",      49.99,   18.00, 4),
    ("4K Monitor 27\"",            "Electronics",     599.99,  340.00, 0),
    ("Mechanical Keyboard",        "Electronics",     149.99,   68.00, 4),
    ("USB-C Docking Station",      "Electronics",     129.99,   55.00, 0),
    ("Noise-Cancelling Headset",   "Electronics",     249.99,  120.00, 4),
    ("Network Switch 24-port",     "Electronics",     899.99,  480.00, 5),
    ("IP Phone System",            "Electronics",     399.99,  200.00, 5),
    # Software → Microsoft (1), SAP (6)
    ("Office 365 Business",        "Software",       1200.00,  400.00, 1),
    ("Azure Cloud (annual)",       "Software",       3600.00, 1700.00, 1),
    ("CRM Suite (annual license)", "Software",       2400.00, 1100.00, 6),
    ("Analytics Platform",         "Software",       3600.00, 1700.00, 6),
    ("HR Management System",       "Software",       1800.00,  830.00, 6),
    ("Cybersecurity Suite",        "Software",       2800.00, 1300.00, 1),
    ("Cloud Storage 1TB/yr",       "Software",        120.00,   40.00, 1),
    # Furniture → Herman Miller (2), Steelcase (3)
    ("Ergonomic Chair Pro",        "Furniture",       699.99,  380.00, 2),
    ("Standing Desk (Electric)",   "Furniture",       899.99,  520.00, 3),
    ("Conference Table 10-seat",   "Furniture",      1499.99,  850.00, 2),
    ("3-Drawer Filing Cabinet",    "Furniture",       299.99,  140.00, 3),
    ("Glass Whiteboard 180cm",     "Furniture",       399.99,  200.00, 2),
    ("Lounge Sofa Set",            "Furniture",      1200.00,  650.00, 3),
    # Services → Accenture (7)
    ("IT Consulting (per day)",    "Services",       1500.00,  800.00, 7),
    ("Employee Training Workshop", "Services",       2500.00, 1200.00, 7),
    ("System Integration Project", "Services",       5000.00, 3000.00, 7),
    ("Data Migration Service",     "Services",       3500.00, 2000.00, 7),
    ("Annual Support Contract",    "Services",       4800.00, 2400.00, 7),
    ("Digital Transformation",     "Services",       8000.00, 4500.00, 7),
]

ENTERPRISE_CUSTOMERS = [
    {"name": "Acme Global Corp",      "industry": "Technology",          "region": "North America", "country": "United States"},
    {"name": "TechFlow Solutions",    "industry": "Information Technology","region": "Europe",       "country": "Germany"},
    {"name": "MediGroup Holdings",    "industry": "Healthcare",           "region": "Asia Pacific",  "country": "Singapore"},
    {"name": "FinTrust International","industry": "Financial Services",   "region": "North America", "country": "Canada"},
    {"name": "RetailCo Group",        "industry": "Retail",               "region": "Europe",        "country": "France"},
    {"name": "BuildCorp Industries",  "industry": "Construction",         "region": "Latin America", "country": "Brazil"},
    {"name": "AeroVision Systems",    "industry": "Aerospace",            "region": "Middle East",   "country": "UAE"},
    {"name": "PharmaCore Labs",       "industry": "Pharmaceuticals",      "region": "North America", "country": "United States"},
    {"name": "DataBridge Analytics",  "industry": "Data & Analytics",     "region": "Europe",        "country": "United Kingdom"},
    {"name": "CloudNine Ventures",    "industry": "Technology",           "region": "Asia Pacific",  "country": "Australia"},
]

NAMED_CONTACTS = {
    "Acme Global Corp": [
        {"name": "James Morrison",  "title": "CEO"},
        {"name": "Linda Park",      "title": "CFO"},
        {"name": "Derek Cho",       "title": "VP Engineering"},
    ],
    "TechFlow Solutions": [
        {"name": "Rachel Torres",   "title": "CTO"},
        {"name": "Kevin Zhao",      "title": "VP Operations"},
    ],
    "MediGroup Holdings": [
        {"name": "Sarah Chen",      "title": "CEO"},
        {"name": "Tom Walsh",       "title": "CTO"},
    ],
    "FinTrust International": [
        {"name": "Robert Clarke",   "title": "CISO"},
        {"name": "Ana Lima",        "title": "CFO"},
    ],
    "RetailCo Group": [
        {"name": "Michael Brown",   "title": "CEO"},
        {"name": "Emma Schmidt",    "title": "Chief Digital Officer"},
    ],
    "BuildCorp Industries": [
        {"name": "Carlos Mendez",   "title": "CEO"},
        {"name": "Patricia Gomez",  "title": "Procurement Director"},
    ],
    "AeroVision Systems": [
        {"name": "Fatima Al-Hassan","title": "CTO"},
        {"name": "Ibrahim Khalil",  "title": "VP Procurement"},
    ],
    "PharmaCore Labs": [
        {"name": "Dr. David Chen",  "title": "CIO"},
        {"name": "Patricia Williams","title": "CFO"},
    ],
    "DataBridge Analytics": [
        {"name": "Alex Thompson",   "title": "CEO"},
        {"name": "Mia Johansson",   "title": "CTO"},
    ],
    "CloudNine Ventures": [
        {"name": "Lucas Wright",    "title": "CEO"},
        {"name": "Nina Patel",      "title": "VP Engineering"},
    ],
}

NAMED_EMPLOYEES = [
    # Sales
    {"name": "Alexandra Chen",   "dept": "Sales",       "role": "VP of Sales",           "salary": 145000},
    {"name": "Marcus Johnson",   "dept": "Sales",       "role": "Account Executive",     "salary": 78000},
    {"name": "Sophie Williams",  "dept": "Sales",       "role": "Account Executive",     "salary": 75000},
    {"name": "David Kim",        "dept": "Sales",       "role": "Sales Development Rep", "salary": 52000},
    {"name": "Priya Nair",       "dept": "Sales",       "role": "Account Executive",     "salary": 80000},
    # Engineering
    {"name": "Ravi Patel",       "dept": "Engineering", "role": "CTO",                   "salary": 185000},
    {"name": "Emma Davis",       "dept": "Engineering", "role": "Senior Engineer",        "salary": 125000},
    {"name": "Oliver Zhang",     "dept": "Engineering", "role": "Tech Lead",             "salary": 135000},
    # Finance
    {"name": "Carlos Rodriguez", "dept": "Finance",     "role": "CFO",                   "salary": 175000},
    {"name": "Natalie Smith",    "dept": "Finance",     "role": "Financial Controller",  "salary": 110000},
    # HR
    {"name": "Isabella Martinez","dept": "HR",          "role": "HR Director",           "salary": 95000},
    {"name": "James Park",       "dept": "HR",          "role": "Recruiter",             "salary": 62000},
    # Operations
    {"name": "Tom Anderson",     "dept": "Operations",  "role": "COO",                   "salary": 165000},
    {"name": "Jasmine Lee",      "dept": "Operations",  "role": "Operations Manager",    "salary": 88000},
    # Marketing
    {"name": "Felix Weber",      "dept": "Marketing",   "role": "CMO",                   "salary": 155000},
    {"name": "Aisha Okonkwo",    "dept": "Marketing",   "role": "Marketing Manager",     "salary": 92000},
]

REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East"]

# ── Helper ─────────────────────────────────────────────────────────────────────

def rdate(days_ago_start=730, days_ago_end=0) -> datetime:
    start = datetime.utcnow() - timedelta(days=days_ago_start)
    end   = datetime.utcnow() - timedelta(days=days_ago_end)
    delta = (end - start).total_seconds()
    return start + timedelta(seconds=random.random() * delta)


# ── Seed functions ─────────────────────────────────────────────────────────────

def seed_suppliers() -> list[dict]:
    db.suppliers.drop()
    docs = []
    for s in NAMED_SUPPLIERS:
        docs.append({
            "_id":           ObjectId(),
            "name":          s["name"],
            "country":       s["country"],
            "lead_time_days":s["lead_time_days"],
            "contact_email": fake.company_email(),
            "is_active":     True,
        })
    db.suppliers.insert_many(docs)
    print(f"  ✓ {len(docs)} suppliers")
    return docs


def seed_products(suppliers: list[dict]) -> list[dict]:
    db.products.drop()
    docs = []
    for i, (name, cat, price, cost, sup_idx) in enumerate(CATALOG):
        margin = round((price - cost) / price * 100, 1)
        docs.append({
            "_id":            ObjectId(),
            "sku":            f"PRD-{i+1:03d}",
            "name":           name,
            "category":       cat,
            "unit_price":     price,
            "cost_price":     cost,
            "margin_pct":     margin,
            "supplier_id":    suppliers[sup_idx]["_id"],
            "supplier_name":  suppliers[sup_idx]["name"],
            "stock_quantity": random.randint(10, 500),
            "is_active":      True,
        })
    db.products.insert_many(docs)
    print(f"  ✓ {len(docs)} products")
    return docs


def seed_employees() -> list[dict]:
    db.employees.drop()

    # C-level names (have manager_id = None)
    c_level_names = {"Ravi Patel", "Carlos Rodriguez", "Isabella Martinez",
                     "Tom Anderson", "Felix Weber", "Alexandra Chen"}
    vp_names      = {"Marcus Johnson", "Sophie Williams", "Priya Nair",
                     "Emma Davis", "Oliver Zhang", "Natalie Smith",
                     "James Park", "Jasmine Lee", "Aisha Okonkwo", "David Kim"}

    named_docs: list[dict] = []
    for e in NAMED_EMPLOYEES:
        doc = {
            "_id":               ObjectId(),
            "name":              e["name"],
            "department":        e["dept"],
            "role":              e["role"],
            "salary":            float(e["salary"]),
            "hire_date":         rdate(2500, 30),
            "manager_id":        None,
            "performance_score": round(random.uniform(3.0, 5.0), 1),
            "is_active":         True,
            "quota":             None,
            "quota_attainment":  None,
        }
        if e["dept"] == "Sales" and e["role"] != "VP of Sales":
            doc["quota"]            = float(random.randint(400, 900)) * 1000
            doc["quota_attainment"] = round(random.uniform(0.6, 1.4), 2)
        named_docs.append(doc)

    # Build name→oid lookup for the named set
    name_to_oid = {d["name"]: d["_id"] for d in named_docs}

    # Assign manager_ids for named employees
    # Map: dept → C-level name
    dept_clevel = {
        "Sales": "Alexandra Chen",
        "Engineering": "Ravi Patel",
        "Finance": "Carlos Rodriguez",
        "HR": "Isabella Martinez",
        "Operations": "Tom Anderson",
        "Marketing": "Felix Weber",
    }
    for doc in named_docs:
        name = doc["name"]
        if name in c_level_names:
            doc["manager_id"] = None
        else:
            mgr_name = dept_clevel.get(doc["department"])
            if mgr_name and mgr_name in name_to_oid:
                doc["manager_id"] = name_to_oid[mgr_name]

    # 34 faker employees
    faker_docs: list[dict] = []
    dept_options = ["Sales", "Engineering", "Finance", "HR", "Operations", "Marketing"]
    dept_roles = {
        "Sales":       ["Account Executive", "Sales Development Rep"],
        "Engineering": ["Software Engineer", "Senior Engineer"],
        "Finance":     ["Accountant", "Financial Analyst"],
        "HR":          ["HR Specialist", "Recruiter"],
        "Operations":  ["Operations Analyst", "Operations Manager"],
        "Marketing":   ["Marketing Specialist", "Content Manager"],
    }
    dept_salary = {
        "Sales":       (45_000, 90_000),
        "Engineering": (65_000, 140_000),
        "Finance":     (55_000, 120_000),
        "HR":          (40_000, 85_000),
        "Operations":  (42_000, 90_000),
        "Marketing":   (45_000, 95_000),
    }
    for _ in range(34):
        dept = random.choice(dept_options)
        lo, hi = dept_salary[dept]
        mgr_name = dept_clevel.get(dept)
        mgr_id = name_to_oid.get(mgr_name) if mgr_name else None
        doc = {
            "_id":               ObjectId(),
            "name":              fake.name(),
            "department":        dept,
            "role":              random.choice(dept_roles[dept]),
            "salary":            round(random.uniform(lo, hi), 2),
            "hire_date":         rdate(2500, 30),
            "manager_id":        mgr_id,
            "performance_score": round(random.uniform(2.5, 5.0), 1),
            "is_active":         random.random() > 0.05,
            "quota":             None,
            "quota_attainment":  None,
        }
        if dept == "Sales":
            doc["quota"]           = float(random.randint(300, 700)) * 1000
            doc["quota_attainment"] = round(random.uniform(0.6, 1.4), 2)
        faker_docs.append(doc)

    all_employees = named_docs + faker_docs
    db.employees.insert_many(all_employees)
    print(f"  ✓ {len(all_employees)} employees")
    return all_employees


def seed_customers(employees: list[dict]) -> list[dict]:
    db.customers.drop()

    # Pick sales employees for account managers
    sales_emps = [e for e in employees if e["department"] == "Sales"]
    if not sales_emps:
        sales_emps = employees[:5]

    docs = []

    # 10 Enterprise (named)
    for ec in ENTERPRISE_CUSTOMERS:
        am = random.choice(sales_emps)
        reg_date = rdate(1800, 90)
        docs.append({
            "_id":               ObjectId(),
            "name":              ec["name"],
            "industry":          ec["industry"],
            "segment":           "Enterprise",
            "region":            ec["region"],
            "country":           ec["country"],
            "account_manager_id":am["_id"],
            "status":            "active",
            "annual_revenue":    round(random.uniform(50e6, 500e6), 2),
            "employee_count":    random.randint(500, 50000),
            "registration_date": reg_date,
            "lifetime_value":    round(random.uniform(200000, 2000000), 2),
            "is_active":         True,
        })

    # 20 SMB (faker company names)
    for _ in range(20):
        am = random.choice(sales_emps)
        reg_date = rdate(1200, 30)
        status = random.choices(["active", "churned", "prospect"], weights=[70, 20, 10])[0]
        docs.append({
            "_id":               ObjectId(),
            "name":              fake.company(),
            "industry":          random.choice(["Retail", "Manufacturing", "Technology", "Healthcare", "Finance"]),
            "segment":           "SMB",
            "region":            random.choice(REGIONS),
            "country":           fake.country(),
            "account_manager_id":am["_id"],
            "status":            status,
            "annual_revenue":    round(random.uniform(1e6, 50e6), 2),
            "employee_count":    random.randint(10, 500),
            "registration_date": reg_date,
            "lifetime_value":    round(random.uniform(5000, 200000), 2),
            "is_active":         status == "active",
        })

    # 50 Individual (faker names)
    for _ in range(50):
        reg_date = rdate(900, 0)
        status = random.choices(["active", "churned", "prospect"], weights=[60, 30, 10])[0]
        docs.append({
            "_id":               ObjectId(),
            "name":              fake.name(),
            "industry":          "Individual",
            "segment":           "Individual",
            "region":            random.choice(REGIONS),
            "country":           fake.country(),
            "account_manager_id":None,
            "status":            status,
            "annual_revenue":    round(random.uniform(0, 500000), 2),
            "employee_count":    1,
            "registration_date": reg_date,
            "lifetime_value":    round(random.uniform(100, 10000), 2),
            "is_active":         status == "active",
        })

    db.customers.insert_many(docs)
    print(f"  ✓ {len(docs)} customers")
    return docs


def seed_contacts(customers: list[dict]) -> list[dict]:
    db.contacts.drop()

    # Build name → customer doc map for enterprise customers
    name_to_customer = {c["name"]: c for c in customers}
    docs = []

    # Named contacts for each enterprise customer
    for comp_name, contacts_list in NAMED_CONTACTS.items():
        cust = name_to_customer.get(comp_name)
        if not cust:
            continue
        for i, ct in enumerate(contacts_list):
            docs.append({
                "_id":               ObjectId(),
                "name":              ct["name"],
                "title":             ct["title"],
                "email":             fake.email(),
                "phone":             fake.phone_number(),
                "company_id":        cust["_id"],
                "company_name":      comp_name,
                "is_primary":        i == 0,
                "last_contact_date": rdate(180, 0),
            })

    # 1 contact per SMB customer
    smb_customers = [c for c in customers if c["segment"] == "SMB"]
    for cust in smb_customers:
        docs.append({
            "_id":               ObjectId(),
            "name":              fake.name(),
            "title":             random.choice(["CEO", "Owner", "General Manager", "Director"]),
            "email":             fake.email(),
            "phone":             fake.phone_number(),
            "company_id":        cust["_id"],
            "company_name":      cust["name"],
            "is_primary":        True,
            "last_contact_date": rdate(365, 0),
        })

    db.contacts.insert_many(docs)
    print(f"  ✓ {len(docs)} contacts")
    return docs


def seed_orders(customers: list[dict], contacts: list[dict],
                employees: list[dict], products: list[dict]) -> list[dict]:
    db.orders.drop()

    # Build lookup maps
    company_to_contacts: dict = {}
    for ct in contacts:
        cid = str(ct["company_id"])
        company_to_contacts.setdefault(cid, []).append(ct)

    sales_emps = [e for e in employees if e["department"] == "Sales"]
    # Named account executives for enterprise bias
    named_ae_names = {"Marcus Johnson", "Sophie Williams", "Priya Nair"}
    named_aes = [e for e in sales_emps if e["name"] in named_ae_names]
    if not named_aes:
        named_aes = sales_emps[:3]

    enterprise_customers = [c for c in customers if c["segment"] == "Enterprise"]
    smb_customers        = [c for c in customers if c["segment"] == "SMB"]
    individual_customers = [c for c in customers if c["segment"] == "Individual"]

    docs = []
    order_num = 1

    def make_order(cust, am, is_enterprise=False, is_smb=False):
        nonlocal order_num
        prod    = random.choice(products)
        qty     = random.randint(5, 50) if is_enterprise else (random.randint(2, 20) if is_smb else random.randint(1, 5))
        disc    = random.choices([0, 0.05, 0.10, 0.15, 0.20], weights=[40, 20, 20, 15, 5])[0]
        total   = round(qty * prod["unit_price"] * (1 - disc), 2)
        o_date  = rdate(730, 0)
        status  = random.choices(["completed", "pending", "cancelled", "refunded"],
                                  weights=[70, 15, 10, 5])[0]
        pay_st  = random.choices(["paid", "invoiced", "overdue"], weights=[65, 25, 10])[0]

        # Find a contact for this customer
        cid_str   = str(cust["_id"])
        ctacts    = company_to_contacts.get(cid_str, [])
        contact   = random.choice(ctacts) if ctacts else None

        docs.append({
            "_id":                ObjectId(),
            "order_number":       f"ORD-{order_num:06d}",
            "customer_id":        cust["_id"],
            "customer_name":      cust["name"],
            "contact_id":         contact["_id"]   if contact else None,
            "contact_name":       contact["name"]  if contact else None,
            "account_manager_id": am["_id"],
            "account_manager_name": am["name"],
            "product_id":         prod["_id"],
            "product_name":       prod["name"],
            "category":           prod["category"],
            "quantity":           qty,
            "unit_price":         prod["unit_price"],
            "discount_pct":       disc,
            "total_amount":       total,
            "region":             cust["region"],
            "status":             status,
            "payment_status":     pay_st,
            "order_date":         o_date,
            "delivery_date":      o_date + timedelta(days=random.randint(1, 21)),
        })
        order_num += 1

    # Enterprise: ~45 orders each, biased to named AEs
    for cust in enterprise_customers:
        n = random.randint(35, 55)
        for _ in range(n):
            am = random.choice(named_aes) if random.random() < 0.7 else random.choice(sales_emps)
            make_order(cust, am, is_enterprise=True)

    # SMB: ~8 orders each
    for cust in smb_customers:
        n = random.randint(5, 12)
        for _ in range(n):
            am = random.choice(sales_emps)
            make_order(cust, am, is_smb=True)

    # Individual: ~2 orders each
    for cust in individual_customers:
        n = random.randint(1, 4)
        for _ in range(n):
            am = random.choice(sales_emps)
            make_order(cust, am)

    # Pad/trim to exactly 900
    while len(docs) < 900:
        cust = random.choice(customers)
        am   = random.choice(sales_emps)
        make_order(cust, am)
    docs = docs[:900]

    db.orders.insert_many(docs)
    print(f"  ✓ {len(docs)} orders")
    return docs


def seed_transactions(employees: list[dict], customers: list[dict],
                      orders: list[dict]) -> list[dict]:
    db.transactions.drop()

    completed_orders = [o for o in orders if o["status"] == "completed"]
    refunded_orders  = [o for o in orders if o["status"] == "refunded"]
    payroll_emps     = [e for e in employees if e.get("is_active")]
    docs = []

    # Revenue: link to completed orders (up to 200)
    rev_orders = random.sample(completed_orders, min(200, len(completed_orders)))
    for o in rev_orders:
        docs.append({
            "_id":         ObjectId(),
            "type":        "revenue",
            "category":    "Sales Revenue",
            "amount":      o["total_amount"],
            "currency":    "USD",
            "date":        o["order_date"],
            "status":      "completed",
            "customer_id": o["customer_id"],
            "order_id":    o["_id"],
            "employee_id": None,
            "description": f"Payment for order {o['order_number']}",
        })

    # Refunds
    ref_orders = random.sample(refunded_orders, min(40, len(refunded_orders)))
    for o in ref_orders:
        docs.append({
            "_id":         ObjectId(),
            "type":        "refund",
            "category":    "Refund",
            "amount":      -abs(o["total_amount"]),
            "currency":    "USD",
            "date":        o["order_date"] + timedelta(days=random.randint(3, 30)),
            "status":      "completed",
            "customer_id": o["customer_id"],
            "order_id":    o["_id"],
            "employee_id": None,
            "description": f"Refund for order {o['order_number']}",
        })

    # Payroll: 1 per employee per month for last 12 months
    for emp in payroll_emps:
        for m in range(12):
            pay_date = datetime.utcnow().replace(day=1) - timedelta(days=30 * m)
            docs.append({
                "_id":         ObjectId(),
                "type":        "expense",
                "category":    "Payroll",
                "amount":      round(emp["salary"] / 12, 2),
                "currency":    "USD",
                "date":        pay_date,
                "status":      "completed",
                "customer_id": None,
                "order_id":    None,
                "employee_id": emp["_id"],
                "description": f"Monthly payroll · {emp['name']}",
            })

    # Operating expenses
    expense_categories = ["Marketing", "Operations", "IT Infrastructure", "Consulting"]
    for i in range(150):
        docs.append({
            "_id":         ObjectId(),
            "type":        "expense",
            "category":    random.choice(expense_categories),
            "amount":      round(random.uniform(500, 50000), 2),
            "currency":    "USD",
            "date":        rdate(730, 0),
            "status":      random.choices(["completed", "pending", "failed"],
                                           weights=[85, 10, 5])[0],
            "customer_id": None,
            "order_id":    None,
            "employee_id": None,
            "description": fake.sentence(),
        })

    # Trim to 600
    random.shuffle(docs)
    docs = docs[:600]
    db.transactions.insert_many(docs)
    print(f"  ✓ {len(docs)} transactions")
    return docs


def create_indexes():
    db.orders.create_index([("order_date",         ASCENDING)])
    db.orders.create_index([("customer_id",        ASCENDING)])
    db.orders.create_index([("account_manager_id", ASCENDING)])
    db.orders.create_index([("product_id",         ASCENDING)])
    db.transactions.create_index([("date",         ASCENDING)])
    db.customers.create_index([("name",            ASCENDING)])
    db.contacts.create_index([("name",             ASCENDING)])
    db.employees.create_index([("name",            ASCENDING)])
    db.suppliers.create_index([("name",            ASCENDING)])
    print("  ✓ indexes created")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Dropping all collections and reseeding BI Wikolabs...")
    for col in ["suppliers", "products", "customers", "contacts", "employees", "orders", "transactions"]:
        db[col].drop()

    suppliers  = seed_suppliers()
    products   = seed_products(suppliers)
    employees  = seed_employees()
    customers  = seed_customers(employees)
    contacts   = seed_contacts(customers)
    orders     = seed_orders(customers, contacts, employees, products)
    seed_transactions(employees, customers, orders)
    create_indexes()
    print("Done!")
