"""Ingestion job and batch tracking tables.

Phase 6 — adds two new tables for tracking Saleor -> Qdrant reindex jobs:

- ``ingestion_jobs`` — one row per ``run_ingestion_job`` Celery task
  (i.e. one per ``POST /admin/reindex`` call).  Tracks overall job
  status, totals, and per-job error.

- ``ingestion_batches`` — N rows per job (one per dispatched
  ``process_batch`` worker task).  Tracks per-batch status, retry
  count, the list of product IDs to embed, and the list of products
  skipped due to permanent errors.

State machines (enforced by CHECK constraints):

- Job:  pending -> processing -> completed
                          -> partial_failed
                          -> failed
- Batch: pending -> processing -> done
                          -> failed

The orchestrator (``run_ingestion_job``) creates a job row + N batch
rows, then dispatches one worker task per batch.  Workers update the
batch row as they process and increment the job's processed/failed
counters; the worker that picks up the final batch updates the job's
status to ``completed`` (no failures) or ``partial_failed`` (>=1 batch
failed).

JSON / JSONB choice
-------------------
The ``product_ids`` and ``skipped_products`` columns use
``JSONB`` rather than ``JSON``.  The columns store arrays of
Saleor product IDs and lists of per-product error metadata, so the
storage format is identical.  ``JSONB`` is preferred because:

1. asyncpg decodes ``JSONB`` to native Python ``list`` / ``dict``
   *only* when an explicit ``set_type_codec('jsonb', …)`` is
   registered on the connection.  ``JSON`` requires the same codec
   but its behaviour in asyncpg is more fragile (it can fall back
   to the raw string even with a registered codec).  ``JSONB`` is
   the documented and tested path.
2. ``JSONB`` supports GIN indexing if we ever want to query by
   product ID or filter on skipped-products contents.
3. ``JSONB`` is the convention in this project for stored
   structured data (see migrations and ``app.db.session``).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the ingestion tracking tables and indexes."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_jobs (
            id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            celery_task_id  VARCHAR(255) UNIQUE NOT NULL,
            status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'processing', 'completed',
                                              'partial_failed', 'failed')),
            total_products  INT          NOT NULL DEFAULT 0,
            total_batches   INT          NOT NULL DEFAULT 0,
            processed_count INT          NOT NULL DEFAULT 0,
            failed_count    INT          NOT NULL DEFAULT 0,
            started_at      TIMESTAMPTZ,
            completed_at    TIMESTAMPTZ,
            error_message   TEXT
        );

        CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_status
            ON ingestion_jobs (status);
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_batches (
            id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id           UUID         NOT NULL
                             REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
            batch_index      INT          NOT NULL,
            status           VARCHAR(20)  NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'processing', 'done', 'failed')),
            product_ids      JSONB        NOT NULL,
            skipped_products JSONB        NOT NULL DEFAULT '[]'::jsonb,
            retry_count      INT          NOT NULL DEFAULT 0,
            error_type       VARCHAR(20),
            error_message    TEXT,
            started_at       TIMESTAMPTZ,
            completed_at     TIMESTAMPTZ,
            UNIQUE (job_id, batch_index)
        );

        CREATE INDEX IF NOT EXISTS ix_ingestion_batches_job_status
            ON ingestion_batches (job_id, status);
    """)


def downgrade() -> None:
    """Drop the ingestion tracking tables in reverse dependency order."""
    op.execute("DROP TABLE IF EXISTS ingestion_batches;")
    op.execute("DROP TABLE IF EXISTS ingestion_jobs;")
