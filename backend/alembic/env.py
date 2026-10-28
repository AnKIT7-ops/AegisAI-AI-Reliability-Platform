"""Alembic environment: online migrations against DATABASE_URL."""

from sqlalchemy import create_engine

from alembic import context
from app.core.logs import configure_logging
from app.db.models import Base
from app.db.session import connect_args, database_url

# Same JSON lines as the server, so container stdout is uniform.
configure_logging("info")

# The backend starts only after this finishes, so it must fail fast.
url = database_url()
engine = create_engine(url, connect_args=connect_args(url))
try:
    with engine.connect() as conn:
        context.configure(
            connection=conn, target_metadata=Base.metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
