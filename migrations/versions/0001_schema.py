from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE users (
        id BIGINT PRIMARY KEY,
        balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE models (
        id SERIAL PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        price INTEGER NOT NULL CHECK (price > 0)
    )
    """,
    """
    CREATE TABLE legs (
        id SERIAL PRIMARY KEY,
        model_id INTEGER NOT NULL REFERENCES models (id),
        provider TEXT NOT NULL,
        priority INTEGER NOT NULL,
        max_ref_images INTEGER NOT NULL CHECK (max_ref_images >= 0),
        skip_on_moderation BOOLEAN NOT NULL DEFAULT false,
        UNIQUE (model_id, priority)
    )
    """,
    """
    CREATE TABLE tasks (
        id UUID PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users (id),
        model_id INTEGER NOT NULL REFERENCES models (id),
        prompt TEXT NOT NULL,
        ref_images JSONB NOT NULL DEFAULT '[]',
        price INTEGER NOT NULL CHECK (price > 0),
        status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
        current_attempt_id UUID,
        result_url TEXT,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX tasks_user_processing ON tasks (user_id) WHERE status = 'processing'",
    """
    CREATE TABLE attempts (
        id UUID PRIMARY KEY,
        task_id UUID NOT NULL REFERENCES tasks (id),
        leg_id INTEGER NOT NULL REFERENCES legs (id),
        provider TEXT NOT NULL,
        priority INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('sending', 'waiting', 'completed', 'failed', 'timed_out', 'cancelled')
        ),
        external_id TEXT,
        error_type TEXT CHECK (error_type IN ('technical', 'moderation', 'payload')),
        error_detail TEXT,
        result_url TEXT,
        late BOOLEAN NOT NULL DEFAULT false,
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        deadline_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX attempts_task ON attempts (task_id, started_at)",
    "CREATE INDEX attempts_open_deadline ON attempts (deadline_at) WHERE status IN ('sending', 'waiting')",
    "CREATE UNIQUE INDEX attempts_external ON attempts (provider, external_id) WHERE external_id IS NOT NULL",
    """
    ALTER TABLE tasks
        ADD CONSTRAINT tasks_current_attempt_fk
        FOREIGN KEY (current_attempt_id) REFERENCES attempts (id)
    """,
    """
    CREATE TABLE payments (
        payment_id TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users (id),
        tokens BIGINT NOT NULL CHECK (tokens > 0),
        status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE ledger (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users (id),
        amount BIGINT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('payment', 'charge', 'refund')),
        task_id UUID REFERENCES tasks (id),
        payment_id TEXT REFERENCES payments (payment_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK ((kind = 'charge' AND amount < 0) OR (kind <> 'charge' AND amount > 0)),
        CHECK ((kind = 'payment') = (payment_id IS NOT NULL)),
        CHECK ((kind IN ('charge', 'refund')) = (task_id IS NOT NULL))
    )
    """,
    "CREATE UNIQUE INDEX ledger_one_credit_per_payment ON ledger (payment_id) WHERE kind = 'payment'",
    "CREATE UNIQUE INDEX ledger_one_entry_per_task_kind ON ledger (task_id, kind) WHERE task_id IS NOT NULL",
    "CREATE INDEX ledger_user_recent ON ledger (user_id, id DESC)",
    """
    CREATE FUNCTION ledger_apply() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        UPDATE users SET balance = balance + NEW.amount WHERE id = NEW.user_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'user % does not exist', NEW.user_id;
        END IF;
        RETURN NEW;
    END
    $$
    """,
    "CREATE TRIGGER ledger_apply AFTER INSERT ON ledger FOR EACH ROW EXECUTE FUNCTION ledger_apply()",
    """
    CREATE FUNCTION ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'ledger is append-only';
    END
    $$
    """,
    """
    CREATE TRIGGER ledger_append_only BEFORE UPDATE OR DELETE ON ledger
        FOR EACH ROW EXECUTE FUNCTION ledger_append_only()
    """,
    """
    CREATE FUNCTION users_balance_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'INSERT' AND NEW.balance <> 0 THEN
            RAISE EXCEPTION 'balance can only be changed through the ledger';
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.balance IS DISTINCT FROM OLD.balance AND pg_trigger_depth() < 2 THEN
            RAISE EXCEPTION 'balance can only be changed through the ledger';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER users_balance_guard BEFORE INSERT OR UPDATE OF balance ON users
        FOR EACH ROW EXECUTE FUNCTION users_balance_guard()
    """,
]

DOWNGRADE = [
    "DROP TABLE ledger",
    "DROP TABLE payments",
    "ALTER TABLE tasks DROP CONSTRAINT tasks_current_attempt_fk",
    "DROP TABLE attempts",
    "DROP TABLE tasks",
    "DROP TABLE legs",
    "DROP TABLE models",
    "DROP TABLE users",
    "DROP FUNCTION ledger_apply()",
    "DROP FUNCTION ledger_append_only()",
    "DROP FUNCTION users_balance_guard()",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
