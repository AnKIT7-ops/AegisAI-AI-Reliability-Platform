"""releases

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Everything copied from the run, plus identity. Never changes after creation.
SNAPSHOT = """
    (NEW.id, NEW.version, NEW.source, NEW.evaluation_run_id,
     NEW.dataset_version, NEW.dataset_checksum, NEW.prompt_version, NEW.chat_model,
     NEW.embed_model, NEW.top_k, NEW.min_score, NEW.config::jsonb, NEW.metrics::jsonb,
     NEW.created_at)
    IS DISTINCT FROM
    (OLD.id, OLD.version, OLD.source, OLD.evaluation_run_id,
     OLD.dataset_version, OLD.dataset_checksum, OLD.prompt_version, OLD.chat_model,
     OLD.embed_model, OLD.top_k, OLD.min_score, OLD.config::jsonb, OLD.metrics::jsonb,
     OLD.created_at)
"""

# Written once by the canary. Without this, direct SQL could flip a failed gate
# to passed and then satisfy the promotion rule.
VERDICT = """
    OLD.gate_passed IS NOT NULL AND
    (NEW.gate_passed, NEW.gate_failures::jsonb, NEW.baseline_release_id,
     NEW.comparison::jsonb)
    IS DISTINCT FROM
    (OLD.gate_passed, OLD.gate_failures::jsonb, OLD.baseline_release_id,
     OLD.comparison::jsonb)
"""


def upgrade() -> None:
    # Batch mode: SQLite cannot add a check constraint in place.
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.add_column(
            sa.Column(
                "source", sa.String(length=16), server_default="live", nullable=False
            )
        )
        batch.create_check_constraint(
            op.f("ck_evaluation_runs_source"), "source IN ('live', 'demo')"
        )

    op.create_table(
        "releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version", sa.String(length=32), nullable=False),
        sa.Column("dataset_checksum", sa.String(length=16), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("chat_model", sa.String(length=128), nullable=False),
        sa.Column("embed_model", sa.String(length=128), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("min_score", sa.Double(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("gate_passed", sa.Boolean(), nullable=True),
        sa.Column("gate_failures", sa.JSON(), nullable=False),
        sa.Column("baseline_release_id", sa.Uuid(), nullable=True),
        sa.Column("comparison", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'canary_passed', 'rejected', 'active', "
            "'superseded', 'rolled_back')",
            name=op.f("ck_releases_status"),
        ),
        sa.CheckConstraint(
            "source IN ('live', 'demo')", name=op.f("ck_releases_source")
        ),
        sa.CheckConstraint(
            "(status = 'candidate' AND gate_passed IS NULL)"
            " OR (status = 'rejected' AND gate_passed IS FALSE)"
            " OR (status IN ('canary_passed', 'active', 'superseded', 'rolled_back')"
            " AND gate_passed IS TRUE)",
            name=op.f("ck_releases_promotion_rule"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_releases_evaluation_run_id_evaluation_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_release_id"],
            ["releases.id"],
            name=op.f("fk_releases_baseline_release_id_releases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_releases")),
        sa.UniqueConstraint("source", "version", name=op.f("uq_releases_source")),
    )
    op.create_index(
        "uq_releases_one_active",
        "releases",
        ["source"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "release_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=True),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["releases.id"],
            name=op.f("fk_release_events_release_id_releases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_events")),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"""
            CREATE FUNCTION releases_guard() RETURNS trigger AS $$
            BEGIN
                IF {SNAPSHOT} THEN
                    RAISE EXCEPTION 'release snapshots cannot change';
                END IF;
                IF {VERDICT} THEN
                    RAISE EXCEPTION 'a canary result cannot change';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER releases_guard BEFORE UPDATE ON releases
            FOR EACH ROW EXECUTE FUNCTION releases_guard()
        """)
        op.execute("""
            CREATE FUNCTION release_events_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'release events are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER release_events_append_only
            BEFORE UPDATE OR DELETE ON release_events
            FOR EACH ROW EXECUTE FUNCTION release_events_append_only()
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER release_events_append_only ON release_events")
        op.execute("DROP FUNCTION release_events_append_only()")
        op.execute("DROP TRIGGER releases_guard ON releases")
        op.execute("DROP FUNCTION releases_guard()")
    op.drop_table("release_events")
    op.drop_index("uq_releases_one_active", table_name="releases")
    op.drop_table("releases")
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_constraint(op.f("ck_evaluation_runs_source"), type_="check")
        batch.drop_column("source")
