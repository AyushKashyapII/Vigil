"""deliberate schema flaws

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22

Applies the deliberate schema flaws Vigil is meant to detect, exactly as
the old app/db_init.py did:
  - a duplicate index on products.category
  - an index on users.last_login_at that nothing ever queries
  - an HNSW index on product_embeddings built while the table is still
    empty (reproduces the real pgvector bug where an index built with no
    data gets garbage centroids)

IMPORTANT: this must run before seed.py, same as app/db_init.py's old
ordering requirement -- the empty-table HNSW flaw only reproduces if the
index exists before any embeddings are inserted.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Duplicate index: two indexes doing the exact same job.
    op.execute("CREATE INDEX idx_products_category ON products(category)")
    op.execute("CREATE INDEX idx_products_cat_dup ON products(category)")

    # Index that nothing ever queries against.
    op.execute("CREATE INDEX idx_users_last_login ON users(last_login_at)")

    # HNSW index built on an empty table -- garbage centroids on purpose.
    op.execute(
        "CREATE INDEX idx_embeddings_hnsw "
        "ON product_embeddings USING hnsw (embedding vector_l2_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_embeddings_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_users_last_login")
    op.execute("DROP INDEX IF EXISTS idx_products_cat_dup")
    op.execute("DROP INDEX IF EXISTS idx_products_category")
