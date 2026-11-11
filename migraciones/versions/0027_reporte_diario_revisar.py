"""Lo que hay que revisar de cada Close Store cargado.

Por día, lo que la lectura no pudo leer o no cerró (departamentos dudosos,
campos de Store Info sin leer, ONLINE/SKOFF de Lottery): el "!" de Ventas
por Departamento y las marcas al editar el día (pedido del usuario,
2026-11-11).

Revision ID: 0027_reporte_diario_revisar
Revises: 0026_fisico_fotos_lectura
Create Date: 2026-11-11
"""

from alembic import op

revision = "0027_reporte_diario_revisar"
down_revision = "0026_fisico_fotos_lectura"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE reportes_diarios.daily_report_review (
            date TEXT COLLATE "C",
            issues_json TEXT COLLATE "C" NOT NULL,
            updated_at TEXT COLLATE "C",
            PRIMARY KEY (date)
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE reportes_diarios.daily_report_review")
