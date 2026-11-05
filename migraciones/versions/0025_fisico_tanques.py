"""Inventario final real por tanque en Físico.

Regular y Premium del ticket del inventario, para ver qué suma cada uno
(pedido del usuario, 2026-11-05). El total sigue en real_ending_gallons.

Revision ID: 0025_fisico_tanques
Revises: 0024_fisico_fotos
Create Date: 2026-11-05
"""

from alembic import op

revision = "0025_fisico_tanques"
down_revision = "0024_fisico_fotos"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE fisico.fisico_months ADD COLUMN real_regular_gallons DOUBLE PRECISION")
    op.execute("ALTER TABLE fisico.fisico_months ADD COLUMN real_premium_gallons DOUBLE PRECISION")


def downgrade():
    op.execute("ALTER TABLE fisico.fisico_months DROP COLUMN real_premium_gallons")
    op.execute("ALTER TABLE fisico.fisico_months DROP COLUMN real_regular_gallons")
