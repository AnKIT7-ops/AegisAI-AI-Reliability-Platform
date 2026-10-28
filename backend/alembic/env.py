"""Alembic environment: online migrations against DATABASE_URL."""

from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context
from app.db.models import Base
from app.db.session import connect_args, database_url

if context.config.config_file_name:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

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
