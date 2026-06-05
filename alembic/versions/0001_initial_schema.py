"""Initial database schema.

Creates the application-managed tables:
- ``threads``:          Chat session metadata (user_id, title, status, timestamps).
- ``generated_images``: Images created inline by the ImageGenerationNode.

LangGraph tables (checkpoints, stores, migrations) are managed
separately by ``langgraph-checkpoint-postgres`` via ``.setup()`` calls
inside the application lifespan — do NOT recreate them here.

Column notes
------------
threads.status
    Lifecycle state machine: ``idle`` → ``busy`` (run start) → ``idle``
    (run complete) → ``deleting`` (DELETE request).  Used to return
    409 Conflict on concurrent runs (FR-013, FR-014).

threads.title_generated
    Set to ``TRUE`` once the title is finalised (LLM success or
    truncation fallback).  Prevents further title updates (FR-024).

threads.title_generation_attempts
    Counter incremented on each LLM title-generation attempt.  Capped
    at ``TITLE_GENERATION_MAX_ATTEMPTS`` (default 3) before falling back
    to truncation (FR-023).

threads.last_activity_at
    Updated on every chat run.  Used by the nightly Celery cleanup job
    to expire threads inactive for > 30 days (FR-018).

generated_images.request_message_id
    ``HumanMessage.id`` of the turn that triggered image generation.
    Links the image to the correct turn in thread history (FR-020, FR-051).
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create application tables."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id                        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                   TEXT        NOT NULL,
            title                     TEXT,
            status                    TEXT        NOT NULL DEFAULT 'idle'
                                                  CHECK (status IN ('idle', 'busy', 'deleting')),
            title_generated           BOOLEAN     NOT NULL DEFAULT FALSE,
            title_generation_attempts SMALLINT    NOT NULL DEFAULT 0,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_activity_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS ix_threads_user_id
            ON threads (user_id);

        CREATE INDEX IF NOT EXISTS ix_threads_last_activity_at
            ON threads (last_activity_at);
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS generated_images (
            id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id          UUID        NOT NULL REFERENCES threads (id) ON DELETE CASCADE,
            user_id            TEXT        NOT NULL,
            prompt             TEXT        NOT NULL,
            s3_key             TEXT        NOT NULL,
            s3_url             TEXT        NOT NULL,
            model              TEXT        NOT NULL,
            request_message_id TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS ix_generated_images_thread_id
            ON generated_images (thread_id);

        CREATE INDEX IF NOT EXISTS ix_generated_images_user_id_date
            ON generated_images (user_id, created_at);

        CREATE INDEX IF NOT EXISTS ix_generated_images_request_message_id
            ON generated_images (request_message_id);
    """)


def downgrade() -> None:
    """Drop application tables in reverse dependency order."""
    op.execute("DROP TABLE IF EXISTS generated_images;")
    op.execute("DROP TABLE IF EXISTS threads;")
