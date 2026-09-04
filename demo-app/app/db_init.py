"""
Applies deliberate schema flaws that Vigil is meant to detect:
  - a duplicate index on products.category
  - an index on users.last_login_at that nothing ever queries
  - an HNSW index on product_embeddings built while the table is still
    empty (reproduces the real pgvector bug where an index built with no
    data gets garbage centroids)

IMPORTANT: run this BEFORE seed.py. The empty-table HNSW flaw only
reproduces if the index exists before any embeddings are inserted, so this
must run first even though it means the app has no data yet.

Run from the demo-app/ directory as:
    python -m app.db_init
"""

from sqlalchemy import text

from app import models  # noqa: F401  (registers models with Base.metadata)
from app.database import Base, engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        # Duplicate index: two indexes doing the exact same job.
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);")
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_products_cat_dup ON products(category);")
        )

        # Index that nothing ever queries against.
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_users_last_login ON users(last_login_at);")
        )

        # HNSW index built on an empty table -- garbage centroids on purpose.
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw "
                "ON product_embeddings USING hnsw (embedding vector_l2_ops);"
            )
        )

    print("db_init: schema flaws applied.")


if __name__ == "__main__":
    main()
