"""add email and phone verification

Revision ID: 71e34cb690e2
Revises: deb0b01524c2
Create Date: 2025-05-07 23:39:31.826443

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71e34cb690e2'
down_revision: Union[str, None] = 'deb0b01524c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('profile', sa.Column('is_phone_verified', sa.Boolean(), nullable=False))
    op.add_column('profile', sa.Column('phone_verification_code', sa.String(), nullable=True))
    op.add_column('profile', sa.Column('phone_verification_expires', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('reset_token', sa.String(), nullable=True))
    op.add_column('users', sa.Column('reset_token_expires', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('is_email_verified', sa.Boolean(), nullable=False))
    op.add_column('users', sa.Column('email_verification_code', sa.String(), nullable=True))
    op.add_column('users', sa.Column('email_verification_expires', sa.DateTime(), nullable=True))
    op.create_unique_constraint(None, 'users', ['reset_token'])
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'users', type_='unique')
    op.drop_column('users', 'email_verification_expires')
    op.drop_column('users', 'email_verification_code')
    op.drop_column('users', 'is_email_verified')
    op.drop_column('users', 'reset_token_expires')
    op.drop_column('users', 'reset_token')
    op.drop_column('profile', 'phone_verification_expires')
    op.drop_column('profile', 'phone_verification_code')
    op.drop_column('profile', 'is_phone_verified')
    # ### end Alembic commands ###
