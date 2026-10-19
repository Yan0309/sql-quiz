"""Load the four Olist tables into public.* if they don't exist yet.

The data ships with this repo (data/olist/*.csv.gz — the Kaggle "Brazilian
E-Commerce Public Dataset by Olist", CC BY-NC-SA 4.0), so GitHub Actions runs
this automatically on a fresh Supabase project. Nothing to download.

Usage:
  DATABASE_URL=... python scripts/load_olist.py            # uses data/olist
  DATABASE_URL=... python scripts/load_olist.py some/dir   # .csv or .csv.gz

A table that already exists is left untouched (never dropped, never appended
to), so this is safe to run on every CI run. All tables are created in ONE
transaction: if anything fails, nothing is left half-loaded.
"""
import gzip
import os
import pathlib
import sys

import psycopg
from psycopg import sql

TABLES = {
    # table: (csv file, CREATE TABLE body)
    "customers": ("olist_customers_dataset.csv", """
        customer_id               text PRIMARY KEY,
        customer_unique_id        text NOT NULL,
        customer_zip_code_prefix  text,
        customer_city             text,
        customer_state            text"""),
    "orders": ("olist_orders_dataset.csv", """
        order_id                       text PRIMARY KEY,
        customer_id                    text NOT NULL REFERENCES customers(customer_id),
        order_status                   text,
        order_purchase_timestamp       timestamp,
        order_approved_at              timestamp,
        order_delivered_carrier_date   timestamp,
        order_delivered_customer_date  timestamp,
        order_estimated_delivery_date  timestamp"""),
    "products": ("olist_products_dataset.csv", """
        product_id                  text PRIMARY KEY,
        product_category_name       text,
        product_name_lenght         integer,
        product_description_lenght  integer,
        product_photos_qty          integer,
        product_weight_g            integer,
        product_length_cm           integer,
        product_height_cm           integer,
        product_width_cm            integer"""),
    "order_items": ("olist_order_items_dataset.csv", """
        order_id             text NOT NULL REFERENCES orders(order_id),
        order_item_id        integer NOT NULL,
        product_id           text NOT NULL REFERENCES products(product_id),
        seller_id            text,
        shipping_limit_date  timestamp,
        price                numeric(10,2),
        freight_value        numeric(10,2),
        PRIMARY KEY (order_id, order_item_id)"""),
}


ROOT = pathlib.Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv  # optional, for local runs
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def open_csv(folder: pathlib.Path, name: str):
    for path, opener in ((folder / name, open), (folder / f"{name}.gz", gzip.open)):
        if path.exists():
            return opener(path, "rb")
    sys.exit(f"Missing {folder / name} (or .gz)")


def main() -> None:
    if len(sys.argv) > 2:
        sys.exit(__doc__)
    folder = pathlib.Path(sys.argv[1]) if len(sys.argv) == 2 else ROOT / "data" / "olist"
    url = os.environ.get("DATABASE_URL") or sys.exit("Set DATABASE_URL")

    with psycopg.connect(url, prepare_threshold=None, connect_timeout=15) as conn:
        conn.execute("SET statement_timeout = '10min'")   # bulk COPY can take a while
        for table, (csv_name, body) in TABLES.items():   # dict order respects FKs
            exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL",
                                  (f"public.{table}",)).fetchone()[0]
            if exists:
                print(f"public.{table}: already exists — left untouched")
                continue
            conn.execute(sql.SQL("CREATE TABLE public.{} ({})").format(
                sql.Identifier(table), sql.SQL(body)))
            copy = sql.SQL("COPY public.{} FROM STDIN WITH (FORMAT csv, HEADER true)").format(
                sql.Identifier(table))
            with conn.cursor().copy(copy) as cp, open_csv(folder, csv_name) as fh:
                while chunk := fh.read(1 << 20):
                    cp.write(chunk)
            # Supabase auto-grants new public tables to its API roles; lock
            # this data away from the Data API (publishable/anon key). The
            # quiz reads it through its own quiz_taker role instead
            # (setup_db.py adds a SELECT-only RLS policy for that role).
            conn.execute(sql.SQL("ALTER TABLE public.{} ENABLE ROW LEVEL SECURITY").format(
                sql.Identifier(table)))
            for api_role in ("anon", "authenticated"):
                if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s",
                                (api_role,)).fetchone():
                    conn.execute(sql.SQL("REVOKE ALL ON public.{} FROM {}").format(
                        sql.Identifier(table), sql.Identifier(api_role)))
            conn.execute(sql.SQL("ANALYZE public.{}").format(sql.Identifier(table)))
            n = conn.execute(sql.SQL("SELECT count(*) FROM public.{}").format(
                sql.Identifier(table))).fetchone()[0]
            print(f"public.{table}: created, {n:,} rows (RLS on, hidden from Data API)")
    print("Done.")


if __name__ == "__main__":
    main()
