from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = [
    "INSERT INTO models (slug, price) VALUES ('image-fast', 2), ('video-long', 25)",
    """
    INSERT INTO legs (model_id, provider, priority, max_ref_images, skip_on_moderation)
    SELECT m.id, v.provider, v.priority, v.max_ref_images, v.skip_on_moderation
    FROM (VALUES
        ('image-fast', 'alpha', 1, 4, false),
        ('image-fast', 'beta', 2, 10, false),
        ('video-long', 'beta', 1, 2, false),
        ('video-long', 'alpha', 2, 1, true)
    ) AS v (slug, provider, priority, max_ref_images, skip_on_moderation)
    JOIN models m ON m.slug = v.slug
    """,
]

DOWNGRADE = [
    "DELETE FROM legs WHERE model_id IN (SELECT id FROM models WHERE slug IN ('image-fast', 'video-long'))",
    "DELETE FROM models WHERE slug IN ('image-fast', 'video-long')",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
