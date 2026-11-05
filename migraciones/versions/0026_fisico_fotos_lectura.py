"""Lo leído de cada foto del inventario en Físico.

La fecha y los dos tanques que se leyeron del ticket, para llenar T1 y T2 al
editar el inventario final real (pedido del usuario, 2026-11-05). read_at
queda en NULL en las fotos subidas antes: la app las lee la primera vez que
se abre el control.

Revision ID: 0026_fisico_fotos_lectura
Revises: 0025_fisico_tanques
Create Date: 2026-11-05
"""

from alembic import op

revision = "0026_fisico_fotos_lectura"
down_revision = "0025_fisico_tanques"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE fisico.fisico_photos ADD COLUMN read_at TEXT")
    op.execute("ALTER TABLE fisico.fisico_photos ADD COLUMN reading_date TEXT")
    op.execute("ALTER TABLE fisico.fisico_photos ADD COLUMN reading_regular DOUBLE PRECISION")
    op.execute("ALTER TABLE fisico.fisico_photos ADD COLUMN reading_premium DOUBLE PRECISION")


def downgrade():
    for column in ("reading_premium", "reading_regular", "reading_date", "read_at"):
        op.execute(f"ALTER TABLE fisico.fisico_photos DROP COLUMN {column}")
