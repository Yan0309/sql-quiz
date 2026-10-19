# Olist data (bundled)

Four tables from the **Brazilian E-Commerce Public Dataset by Olist**
(Olist, via Kaggle: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce),
licensed **CC BY-NC-SA 4.0**. Files are unmodified except for gzip compression.
Used here for non-commercial coursework, with attribution; any redistribution
of these files must keep the same license.

| File | Rows | SHA-256 of the .gz |
|---|---|---|
| olist_customers_dataset.csv.gz | 99,441 | f7d1fa0d061ab53ade0c4f98550ef533926c728eb3d519001b4919e47471b809 |
| olist_orders_dataset.csv.gz | 99,441 | 7dc9717658bdf1b9f4295995ae9565582ab5b916714d71201550c1a15ddc82da |
| olist_order_items_dataset.csv.gz | 112,650 | bba42b351bff59704023e85ce034f7ca2d03d7ecbcd0aa6175c71d53444c2cd4 |
| olist_products_dataset.csv.gz | 32,951 | 3c2175b61e3e3fe5e006837b54cc1349456aa4f8dc1cd43a2adfedc7c6038327 |

`scripts/load_olist.py` loads them into `public.*` (GitHub Actions does this
automatically on the first run).
