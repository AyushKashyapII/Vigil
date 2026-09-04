"""
Seeds the demo database with realistic volumes of data.

Run this AFTER app/db_init.py -- the HNSW index created there needs to be
built on an empty product_embeddings table for the empty-table pgvector
bug to reproduce. Run from the demo-app/ directory as:

    python -m app.db_init
    python seed.py
"""

import random
import time

import numpy as np
from faker import Faker

from app.database import SessionLocal
from app.models import EMBEDDING_DIM, InventoryLog, Order, OrderItem, Product, ProductEmbedding, Review, User

fake = Faker()

NUM_USERS = 2000
NUM_PRODUCTS = 500
NUM_ORDERS = 50000
NUM_REVIEWS = 20000
NUM_INVENTORY_LOGS = 10000

CHUNK_SIZE = 1000

PRODUCT_CATEGORIES = [
    "electronics", "home", "kitchen", "clothing", "toys",
    "books", "sports", "beauty", "grocery", "automotive",
]

ORDER_STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled"]

INVENTORY_REASONS = ["restock", "sale", "return", "damaged", "adjustment"]


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def seed_users(db):
    print(f"seeding {NUM_USERS} users...")
    rows = [
        {
            "email": fake.unique.email(),
            "name": fake.name(),
            "created_at": fake.date_time_between(start_date="-2y", end_date="now"),
            "last_login_at": fake.date_time_between(start_date="-30d", end_date="now"),
        }
        for _ in range(NUM_USERS)
    ]
    for chunk in chunked(rows, CHUNK_SIZE):
        db.bulk_insert_mappings(User, chunk)
        db.commit()
    print("users done.")


def seed_products(db):
    print(f"seeding {NUM_PRODUCTS} products...")
    rows = [
        {
            "name": fake.catch_phrase(),
            "category": random.choice(PRODUCT_CATEGORIES),
            "price": round(random.uniform(5, 500), 2),
            "stock_qty": random.randint(0, 1000),
            "created_at": fake.date_time_between(start_date="-2y", end_date="now"),
        }
        for _ in range(NUM_PRODUCTS)
    ]
    for chunk in chunked(rows, CHUNK_SIZE):
        db.bulk_insert_mappings(Product, chunk)
        db.commit()
    print("products done.")


def seed_embeddings(db):
    print(f"seeding {NUM_PRODUCTS} product embeddings...")
    product_ids = [pid for (pid,) in db.query(Product.id).all()]
    rng = np.random.default_rng()
    rows = []
    for pid in product_ids:
        vec = rng.normal(size=EMBEDDING_DIM)
        vec = vec / np.linalg.norm(vec)
        rows.append({"product_id": pid, "embedding": vec.tolist()})
    for chunk in chunked(rows, CHUNK_SIZE):
        db.bulk_insert_mappings(ProductEmbedding, chunk)
        db.commit()
    print("embeddings done.")


def seed_orders(db):
    print(f"seeding {NUM_ORDERS} orders...")
    user_ids = [uid for (uid,) in db.query(User.id).all()]
    rows = [
        {
            "user_id": random.choice(user_ids),
            "status": random.choice(ORDER_STATUSES),
            "total_amount": round(random.uniform(10, 2000), 2),
            "created_at": fake.date_time_between(start_date="-1y", end_date="now"),
            "updated_at": fake.date_time_between(start_date="-1y", end_date="now"),
        }
        for _ in range(NUM_ORDERS)
    ]
    for i, chunk in enumerate(chunked(rows, CHUNK_SIZE)):
        db.bulk_insert_mappings(Order, chunk)
        db.commit()
        if (i + 1) % 10 == 0:
            print(f"  {(i + 1) * CHUNK_SIZE} orders inserted...")
    print("orders done.")


def seed_order_items(db):
    print("seeding order items (~3 per order)...")
    order_ids = [oid for (oid,) in db.query(Order.id).all()]
    product_rows = db.query(Product.id, Product.price).all()

    rows = []
    total = 0
    for oid in order_ids:
        for _ in range(random.randint(1, 5)):  # averages ~3/order
            product_id, price = random.choice(product_rows)
            rows.append(
                {
                    "order_id": oid,
                    "product_id": product_id,
                    "quantity": random.randint(1, 5),
                    "unit_price": price,
                }
            )
        if len(rows) >= CHUNK_SIZE:
            db.bulk_insert_mappings(OrderItem, rows)
            db.commit()
            total += len(rows)
            rows = []
            if total % (CHUNK_SIZE * 20) == 0:
                print(f"  {total} order items inserted...")
    if rows:
        db.bulk_insert_mappings(OrderItem, rows)
        db.commit()
        total += len(rows)
    print(f"order items done ({total} total).")


def seed_reviews(db):
    print(f"seeding {NUM_REVIEWS} reviews...")
    user_ids = [uid for (uid,) in db.query(User.id).all()]
    product_ids = [pid for (pid,) in db.query(Product.id).all()]
    rows = [
        {
            "product_id": random.choice(product_ids),
            "user_id": random.choice(user_ids),
            "rating": random.randint(1, 5),
            "comment": fake.sentence(),
            "created_at": fake.date_time_between(start_date="-1y", end_date="now"),
        }
        for _ in range(NUM_REVIEWS)
    ]
    for chunk in chunked(rows, CHUNK_SIZE):
        db.bulk_insert_mappings(Review, chunk)
        db.commit()
    print("reviews done.")


def seed_inventory_logs(db):
    print(f"seeding {NUM_INVENTORY_LOGS} inventory logs...")
    product_ids = [pid for (pid,) in db.query(Product.id).all()]
    rows = [
        {
            "product_id": random.choice(product_ids),
            "change_qty": random.randint(-50, 50),
            "reason": random.choice(INVENTORY_REASONS),
            "created_at": fake.date_time_between(start_date="-1y", end_date="now"),
        }
        for _ in range(NUM_INVENTORY_LOGS)
    ]
    for chunk in chunked(rows, CHUNK_SIZE):
        db.bulk_insert_mappings(InventoryLog, chunk)
        db.commit()
    print("inventory logs done.")


def main():
    start = time.time()
    db = SessionLocal()
    try:
        seed_users(db)
        seed_products(db)
        seed_embeddings(db)
        seed_orders(db)
        seed_order_items(db)
        seed_reviews(db)
        seed_inventory_logs(db)
    finally:
        db.close()
    print(f"seed complete in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
