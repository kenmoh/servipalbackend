"""add payment method

Revision ID: 804d2fd94f14
Revises: 6a3125c45c30
Create Date: 2025-07-04 21:13:17.699381

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '804d2fd94f14'
down_revision: Union[str, None] = '6a3125c45c30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create the enum type first
    payment_method_enum = sa.Enum('WALLET', 'CARD', 'BANK_TRANSFER', name='paymentmethod')
    payment_method_enum.create(op.get_bind(), checkfirst=True)
    # Now add the column
    op.add_column('transactions', sa.Column('payment_method', payment_method_enum, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the column first
    op.drop_column('transactions', 'payment_method')
    # Then drop the enum type
    payment_method_enum = sa.Enum('WALLET', 'CARD', 'BANK_TRANSFER', name='paymentmethod')
    payment_method_enum.drop(op.get_bind(), checkfirst=True)
