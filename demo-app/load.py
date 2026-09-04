"""
Standalone load generator -- NOT part of the FastAPI app. Hammers the demo
app with a weighted-random mix of the deliberately-bad endpoints so
pg_stat_statements has something to chew on. Safe to run multiple instances
concurrently in separate terminals.
"""

import random
import time

import requests

BASE_URL = "http://localhost:8000"

NUM_USERS = 2000
NUM_PRODUCTS = 500

# (method, path template, id kind, weight)
ENDPOINTS = [
    ("GET", "/orders-bad", None, 3),
    ("GET", "/orders-by-user/{id}", "user", 4),
    ("GET", "/all-inventory-logs", None, 1),
    ("GET", "/reviews-by-product/{id}", "product", 4),
    ("GET", "/order-summary-nested/{id}", "user", 2),
]


def build_request():
    method, path, id_kind, _weight = random.choices(
        ENDPOINTS, weights=[e[3] for e in ENDPOINTS], k=1
    )[0]
    if id_kind == "user":
        path = path.replace("{id}", str(random.randint(1, NUM_USERS)))
    elif id_kind == "product":
        path = path.replace("{id}", str(random.randint(1, NUM_PRODUCTS)))
    return method, BASE_URL + path


def main():
    print(f"load generator hitting {BASE_URL} -- Ctrl+C to stop")
    while True:
        method, url = build_request()
        try:
            resp = requests.request(method, url, timeout=10)
            print(f"{method} {url} -> {resp.status_code}")
        except requests.RequestException as exc:
            print(f"{method} {url} -> error: {exc}")
        time.sleep(random.uniform(0.05, 0.3))


if __name__ == "__main__":
    main()
