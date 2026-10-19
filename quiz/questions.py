"""The quiz: 5 questions, 20 points, on the Olist e-commerce tables
(public.customers, orders, order_items, products) — the same database used
by the IDS 706 SQL practice lab.

Each question carries a hint = the expected SQL + the expected result.
`expected_rows` is what the grader compares against. CI
(scripts/check_quiz.py) re-runs every `hint_sql` against the live database
and fails if the result differs, so hints cannot silently drift from the data.

Expected results were computed on the full Kaggle Olist dataset (99,441
orders) and verified identical whether the tables were loaded with proper
types (scripts/load_olist.py) or with pandas `to_sql` (text timestamps,
double-precision prices). No question depends on column types or on today's date.
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Question:
    qid: str
    title: str
    points: int
    prompt: str
    hint_sql: str
    expected_columns: tuple
    expected_rows: tuple
    ordered: bool  # True => row order must match too


QUIZ_TITLE = "Olist E-commerce SQL Quiz"

Q5_SQL = (
    "SELECT o.order_status, COUNT(*) AS orders_without_items\n"
    "FROM orders o\n"
    "LEFT JOIN order_items oi ON oi.order_id = o.order_id\n"
    "WHERE oi.order_id IS NULL\n"
    "GROUP BY o.order_status\n"
    "ORDER BY orders_without_items DESC, o.order_status;"
)

QUESTIONS = (
    Question(
        qid="q1",
        title="Orders by status",
        points=3,
        prompt=(
            "How many orders are there in each **order_status**? Show the status and "
            "the count, largest count first (break ties by status A→Z)."
        ),
        hint_sql=(
            "SELECT order_status, COUNT(*) AS order_count\n"
            "FROM orders\n"
            "GROUP BY order_status\n"
            "ORDER BY order_count DESC, order_status;"
        ),
        expected_columns=("order_status", "order_count"),
        expected_rows=(
            ("delivered", 96478),
            ("shipped", 1107),
            ("canceled", 625),
            ("unavailable", 609),
            ("invoiced", 314),
            ("processing", 301),
            ("created", 5),
            ("approved", 2),
        ),
        ordered=True,
    ),
    Question(
        qid="q2",
        title="Top cities by real customers",
        points=4,
        prompt=(
            "In Olist, `customer_id` is created **per order**; the real person is "
            "`customer_unique_id`. List the **5 cities** with the most **distinct real "
            "customers**: show `customer_city` and the count, largest first."
        ),
        hint_sql=(
            "SELECT customer_city,\n"
            "       COUNT(DISTINCT customer_unique_id) AS customers\n"
            "FROM customers\n"
            "GROUP BY customer_city\n"
            "ORDER BY customers DESC, customer_city\n"
            "LIMIT 5;"
        ),
        expected_columns=("customer_city", "customers"),
        expected_rows=(
            ("sao paulo", 14984),
            ("rio de janeiro", 6620),
            ("belo horizonte", 2672),
            ("brasilia", 2069),
            ("curitiba", 1465),
        ),
        ordered=True,
    ),
    Question(
        qid="q3",
        title="Delivered revenue by state",
        points=5,
        prompt=(
            "Consider only orders with `order_status = 'delivered'`. For each customer "
            "state show **customer_state**, the number of **delivered orders**, and the "
            "**item revenue** = sum of `order_items.price` (no freight), rounded to "
            "2 decimals. Return the **top 5 states by revenue**.\n\n"
            "⚠️ Joining to `order_items` repeats an order once per item — count orders carefully."
        ),
        hint_sql=(
            "SELECT c.customer_state,\n"
            "       COUNT(DISTINCT o.order_id)         AS delivered_orders,\n"
            "       ROUND(SUM(oi.price::numeric), 2)   AS item_revenue\n"
            "FROM customers c\n"
            "JOIN orders o       ON o.customer_id = c.customer_id\n"
            "JOIN order_items oi ON oi.order_id   = o.order_id\n"
            "WHERE o.order_status = 'delivered'\n"
            "GROUP BY c.customer_state\n"
            "ORDER BY item_revenue DESC\n"
            "LIMIT 5;"
        ),
        expected_columns=("customer_state", "delivered_orders", "item_revenue"),
        expected_rows=(
            ("SP", 40501, Decimal("5067633.16")),
            ("RJ", 12350, Decimal("1759651.13")),
            ("MG", 11354, Decimal("1552481.83")),
            ("RS", 5345, Decimal("728897.47")),
            ("PR", 4923, Decimal("666063.51")),
        ),
        ordered=True,
    ),
    Question(
        qid="q4",
        title="How often do customers come back?",
        points=4,
        prompt=(
            "For every real customer (`customer_unique_id`), count their orders. Then "
            "show the distribution: **n_orders** and **how many customers** placed "
            "exactly that many orders, ordered by n_orders. (Hint: a CTE or subquery, "
            "then GROUP BY again.)"
        ),
        hint_sql=(
            "WITH per_customer AS (\n"
            "    SELECT c.customer_unique_id, COUNT(*) AS n_orders\n"
            "    FROM customers c\n"
            "    JOIN orders o ON o.customer_id = c.customer_id\n"
            "    GROUP BY c.customer_unique_id\n"
            ")\n"
            "SELECT n_orders, COUNT(*) AS customers\n"
            "FROM per_customer\n"
            "GROUP BY n_orders\n"
            "ORDER BY n_orders;"
        ),
        expected_columns=("n_orders", "customers"),
        expected_rows=(
            (1, 93099),
            (2, 2745),
            (3, 203),
            (4, 30),
            (5, 8),
            (6, 6),
            (7, 3),
            (9, 1),
            (17, 1),
        ),
        ordered=True,
    ),
    Question(
        qid="q5",
        title="Predict the output",
        points=4,
        prompt=(
            "**What does this query return?** Predict first: how many rows, and which "
            "status is on top? Then run it and submit it as your answer.\n\n"
            f"```sql\n{Q5_SQL}\n```"
        ),
        hint_sql=Q5_SQL,
        expected_columns=("order_status", "orders_without_items"),
        expected_rows=(
            ("unavailable", 603),
            ("canceled", 164),
            ("created", 5),
            ("invoiced", 2),
            ("shipped", 1),
        ),
        ordered=True,
    ),
)

TOTAL_POINTS = sum(q.points for q in QUESTIONS)
assert TOTAL_POINTS == 20, f"points must sum to 20, got {TOTAL_POINTS}"

BY_ID = {q.qid: q for q in QUESTIONS}

# Columns the quiz relies on; scripts/setup_db.py checks they exist.
REQUIRED_COLUMNS = {
    "customers": ("customer_id", "customer_unique_id", "customer_city", "customer_state"),
    "orders": ("order_id", "customer_id", "order_status", "order_purchase_timestamp"),
    "order_items": ("order_id", "product_id", "price"),
    "products": ("product_id",),
}
QUIZ_TABLES = tuple(REQUIRED_COLUMNS)

SCHEMA_REFERENCE = """\
customers   (customer_id, customer_unique_id, customer_zip_code_prefix,
             customer_city, customer_state)
orders      (order_id, customer_id → customers, order_status,
             order_purchase_timestamp, order_approved_at,
             order_delivered_carrier_date, order_delivered_customer_date,
             order_estimated_delivery_date)
order_items (order_id → orders, order_item_id, product_id → products,
             seller_id, shipping_limit_date, price, freight_value)
products    (product_id, product_category_name, product_weight_g, ...)

Note: customer_id is one per ORDER; customer_unique_id is the real person."""
