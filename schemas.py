"""Compact MongoDB collection schemas for use in LLM prompts."""

SCHEMAS: dict[str, str] = {
    "sales": """Fields:
  order_id: str — unique order identifier
  customer_id: str — FK → customers.customer_id
  product_id: str — FK → products.product_id
  quantity: int — units ordered
  unit_price: float — price per unit at time of sale
  total_amount: float — quantity * unit_price * (1 - discount)
  discount: float — fractional discount (0.0–1.0)
  region: str — geographic sales region
  sales_rep: str — name of the sales representative
  status: str — "completed" | "pending" | "cancelled"
  order_date: ISODate — when the order was placed
  delivery_date: ISODate — when the order was delivered""",

    "products": """Fields:
  product_id: str — unique product identifier
  name: str — product display name
  category: str — "Electronics" | "Office Supplies" | "Furniture" | "Software" | "Services"
  unit_price: float — current selling price
  cost_price: float — cost of goods sold per unit
  stock_quantity: int — units in inventory
  supplier: str — supplier name
  sku: str — stock-keeping unit code
  is_active: bool — whether the product is currently sold""",

    "customers": """Fields:
  customer_id: str — unique customer identifier
  name: str — company or individual name
  email: str — contact email
  segment: str — "Enterprise" | "SMB" | "Individual"
  region: str — geographic region
  country: str — country name
  registration_date: ISODate — when the customer registered
  total_orders: int — lifetime order count
  total_spent: float — lifetime spend in USD
  is_active: bool — whether the customer is currently active""",

    "employees": """Fields:
  employee_id: str — unique employee identifier
  name: str — full name
  department: str — "Sales" | "Engineering" | "Finance" | "HR" | "Operations"
  role: str — job title
  salary: float — annual salary in USD
  hire_date: ISODate — date of hire
  manager_id: str — FK → employees.employee_id (nullable)
  performance_score: float — annual score on a 1–5 scale
  is_active: bool — currently employed""",

    "transactions": """Fields:
  transaction_id: str — unique transaction identifier
  type: str — "revenue" | "expense" | "refund"
  amount: float — transaction amount in USD
  currency: str — ISO currency code (e.g. "USD")
  date: ISODate — transaction date
  category: str — "Sales" | "Marketing" | "Operations" | "Payroll" | "Refund"
  status: str — "completed" | "pending" | "failed"
  notes: str — free-text description""",
}

COLLECTION_SUMMARIES: dict[str, str] = {
    "sales": (
        "The sales collection records every customer order. Each document represents "
        "a single order line with the customer, product, quantity, pricing, discount, "
        "sales region, and fulfilment status. Use this collection to analyse revenue "
        "trends, order volumes, regional performance, and sales-rep productivity. "
        "Join to products for category/margin analysis or to customers for segment "
        "break-downs."
    ),
    "products": (
        "The products collection is the master product catalogue. Each document "
        "describes one SKU with its current selling price, cost price, category, "
        "supplier, and inventory level. Use this collection to analyse margins, "
        "inventory health, and category mix. Join to sales to see revenue or units "
        "sold per product."
    ),
    "customers": (
        "The customers collection stores business profiles for every buyer. Key "
        "attributes include market segment (Enterprise / SMB / Individual), geographic "
        "region, lifetime order count, and lifetime spend. Use this collection for "
        "segment analysis, customer lifetime value, and geographic breakdowns. Join to "
        "sales for purchase history."
    ),
    "employees": (
        "The employees collection holds HR records for all staff. Each document "
        "captures department, role, salary, hire date, manager, and the most recent "
        "annual performance score. Use this collection for headcount reports, payroll "
        "analysis, performance distribution, and organisational hierarchy queries."
    ),
    "transactions": (
        "The transactions collection is the financial ledger. Each document records a "
        "single financial event — a revenue receipt, operating expense, or refund — "
        "with its category, amount, status, and date. Use this collection for cash-flow "
        "analysis, expense breakdowns, budget tracking, and reconciliation."
    ),
}
