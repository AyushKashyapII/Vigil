import random
import time

import numpy as np
from fastapi import Depends, FastAPI, HTTPException
from faker import Faker
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import EMBEDDING_DIM, InventoryLog, Order, OrderItem, Product, ProductEmbedding, Review, User

app = FastAPI(title="Vigil Demo App")

fake = Faker()

PRODUCT_CATEGORIES = [
    "electronics", "home", "kitchen", "clothing", "toys",
    "books", "sports", "beauty", "grocery", "automotive",
]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/orders-bad")
def orders_bad(db: Session = Depends(get_db)):
    orders = db.query(Order).limit(100).all()
    result = []
    for order in orders:
        # Lazy-loads order.user one query at a time -- N+1 on purpose.
        result.append({"id": order.id, "user_email": order.user.email})
    return result


@app.get("/orders-good")
def orders_good(db: Session = Depends(get_db)):
    rows = (
        db.query(Order.id, User.email)
        .join(User, Order.user_id == User.id)
        .limit(100)
        .all()
    )
    return [{"id": r.id, "user_email": r.email} for r in rows]


@app.get("/order-summary-nested/{user_id}")
def order_summary_nested(user_id: int, db: Session = Depends(get_db)):
    # Correlated scalar subqueries per order, deliberately instead of a
    # single JOIN + GROUP BY.
    item_count_subq = (
        select(func.count(OrderItem.id))
        .where(OrderItem.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )
    total_subq = (
        select(func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0))
        .where(OrderItem.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )

    rows = (
        db.query(Order.id, item_count_subq.label("item_count"), total_subq.label("total"))
        .filter(Order.user_id == user_id)
        .all()
    )
    return [{"order_id": r.id, "item_count": r.item_count, "total": float(r.total)} for r in rows]


@app.get("/orders-by-user/{user_id}")
def orders_by_user(user_id: int, db: Session = Depends(get_db)):
    # Hits the intentionally missing index on orders.user_id.
    orders = db.query(Order).filter(Order.user_id == user_id).all()
    return [
        {
            "id": o.id,
            "user_id": o.user_id,
            "status": o.status,
            "total_amount": float(o.total_amount),
            "created_at": o.created_at,
            "updated_at": o.updated_at,
        }
        for o in orders
    ]


@app.get("/all-inventory-logs")
def all_inventory_logs(db: Session = Depends(get_db)):
    # Deliberately unbounded: no LIMIT, no pagination.
    logs = db.query(InventoryLog).all()
    return [
        {
            "id": log.id,
            "product_id": log.product_id,
            "change_qty": log.change_qty,
            "reason": log.reason,
            "created_at": log.created_at,
        }
        for log in logs
    ]


@app.post("/bulk-import-products")
def bulk_import_products(count: int = 20000, db: Session = Depends(get_db)):
    # Single transaction, single commit at the end -- meant to leave the
    # planner working off stale statistics until the next ANALYZE.
    products = [
        {
            "name": fake.catch_phrase(),
            "category": random.choice(PRODUCT_CATEGORIES),
            "price": round(random.uniform(5, 500), 2),
            "stock_qty": random.randint(0, 1000),
        }
        for _ in range(count)
    ]
    db.bulk_insert_mappings(Product, products)
    db.commit()
    return {"inserted": count}


@app.get("/reviews-by-product/{product_id}")
def reviews_by_product(product_id: int, db: Session = Depends(get_db)):
    # Hits the intentionally missing index on reviews.product_id.
    reviews = db.query(Review).filter(Review.product_id == product_id).all()
    return [
        {
            "id": r.id,
            "product_id": r.product_id,
            "user_id": r.user_id,
            "rating": r.rating,
            "comment": r.comment,
            "created_at": r.created_at,
        }
        for r in reviews
    ]


@app.get("/leaky")
def leaky():
    # Intentional connection leak: this session is opened by hand
    # (bypassing the get_db dependency) and is never closed or returned to
    # the pool. Meant to reproduce "idle in transaction" / connection-leak
    # conditions for Vigil to detect.
    session = SessionLocal()
    count = session.query(func.count(User.id)).scalar()
    return {"user_count": count}


@app.post("/reserve-stock/{product_id}")
def reserve_stock(product_id: int, db: Session = Depends(get_db)):
    # Locks the product first, sleeps to widen the race window, then locks
    # a related order. /log-order locks in the OPPOSITE order (order, then
    # product) -- calling both concurrently reliably deadlocks in Postgres.
    product = db.query(Product).filter(Product.id == product_id).with_for_update().first()
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")

    time.sleep(2)

    order_item = db.query(OrderItem).filter(OrderItem.product_id == product_id).first()
    if order_item is None:
        db.commit()
        raise HTTPException(status_code=404, detail="no related order found")

    order = db.query(Order).filter(Order.id == order_item.order_id).with_for_update().first()
    order.status = "reserved"
    product.stock_qty = max(product.stock_qty - 1, 0)
    db.commit()
    return {"product_id": product_id, "order_id": order.id}


@app.post("/log-order/{order_id}")
def log_order(order_id: int, db: Session = Depends(get_db)):
    # Locks the order first, sleeps, then locks a related product --
    # OPPOSITE lock order from /reserve-stock (which locks product then
    # order). Calling both endpoints concurrently on overlapping rows
    # reliably produces a Postgres deadlock, since each transaction ends up
    # waiting on a lock the other one holds.
    order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    time.sleep(2)

    order_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).first()
    if order_item is None:
        db.commit()
        raise HTTPException(status_code=404, detail="no related product found")

    product = db.query(Product).filter(Product.id == order_item.product_id).with_for_update().first()
    product.stock_qty = max(product.stock_qty - 1, 0)
    order.status = "logged"
    db.commit()
    return {"order_id": order_id, "product_id": product.id}


@app.post("/slow-transaction")
def slow_transaction(db: Session = Depends(get_db)):
    # Holds a transaction (and its pooled connection) open for 5s -- hammer
    # this concurrently to exhaust the connection pool.
    db.execute(text("SELECT pg_sleep(5);"))
    db.commit()
    return {"status": "done"}


@app.post("/simulate-churn")
def simulate_churn(iterations: int = 2000, db: Session = Depends(get_db)):
    # Rapid insert/delete churn to generate dead tuples quickly for bloat
    # testing. Deliberately never VACUUMs -- that would defeat the point.
    product_ids = [pid for (pid,) in db.query(Product.id).all()]
    if not product_ids:
        raise HTTPException(status_code=400, detail="no products seeded yet")

    for _ in range(iterations):
        log = InventoryLog(
            product_id=random.choice(product_ids),
            change_qty=random.randint(-10, 10),
            reason="churn-test",
        )
        db.add(log)
        db.commit()

        victim = db.query(InventoryLog).order_by(func.random()).limit(1).first()
        if victim is not None:
            db.delete(victim)
            db.commit()

    return {"iterations": iterations}


@app.get("/search-similar-products")
def search_similar_products(q: str, db: Session = Depends(get_db)):
    # NOTE: this generates a random unit vector as a stand-in for a real
    # embedding. A real implementation would call an embedding model (e.g.
    # sentence-transformers, or an API) on `q` right here.
    rng = np.random.default_rng()
    vec = rng.normal(size=EMBEDDING_DIM)
    vec = vec / np.linalg.norm(vec)

    rows = (
        db.query(
            Product.id,
            Product.name,
            ProductEmbedding.embedding.l2_distance(vec).label("distance"),
        )
        .join(ProductEmbedding, ProductEmbedding.product_id == Product.id)
        .order_by(ProductEmbedding.embedding.l2_distance(vec))
        .limit(10)
        .all()
    )
    return [{"id": r.id, "name": r.name, "distance": float(r.distance)} for r in rows]
