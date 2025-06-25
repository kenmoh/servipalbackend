from uuid import UUID
from decimal import Decimal
from app.database.database import Base
from sqlalchemy.orm import Mapped, mapped_column


class VendorReviewStats(Base):
    __tablename__ = "vendor_review_stats"
    __table_args__ = {"extend_existing": True}
    __table_args__ = {"info": {"is_view": True}}

    vendor_id: Mapped[UUID] = mapped_column(primary_key=True)
    item_type: Mapped[str]
    avg_rating: Mapped[Decimal]
    review_count: Mapped[int]
