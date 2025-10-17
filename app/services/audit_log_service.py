from datetime import datetime
from typing import Optional, List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models.models import AuditLog
from app.models.models import TransactiontLogs
from fastapi import HTTPException, status

from app.schemas.audit_logs import AuditLogResponse
from app.schemas.status_schema import TransactionLogAction, TransactionLogStatus


class AuditLogService:
    @staticmethod
    async def get_logs(
        db: AsyncSession,
        *,
        actor_id: Optional[UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[UUID] = None,
        action: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AuditLogResponse]:
        """
        Query audit logs with optional filters.
        Args:
            db: Database session
            actor_id: Filter by actor
            resource_type: Filter by resource type
            resource_id: Filter by resource id
            action: Filter by action
            start_time: Filter logs after this time
            end_time: Filter logs before this time
            limit: Max number of logs to return
            offset: Offset for pagination
        Returns:
            List of AuditLog entries
        """
        stmt = select(AuditLog)
        conditions = []
        if actor_id:
            conditions.append(AuditLog.actor_id == actor_id)
        if resource_type:
            conditions.append(AuditLog.resource_type == resource_type)
        if resource_id:
            conditions.append(AuditLog.resource_id == resource_id)
        if action:
            conditions.append(AuditLog.action == action)
        if start_time:
            conditions.append(AuditLog.timestamp >= start_time)
        if end_time:
            conditions.append(AuditLog.timestamp <= end_time)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit)
        result = await db.execute(stmt)

        return result.scalars().all()

    @staticmethod
    async def get_log_by_id(db: AsyncSession, log_id: UUID) -> list[AuditLogResponse]:
        """
        Fetch a user audit logs.
        Args:
            db: Database session
            log_id: UUID of the log
        Returns:
            list of AuditLog instance or []

        """
        stmt = select(AuditLog).where(AuditLog.id == log_id)
        result = await db.execute(stmt)
        log = result.scalar_one_or_none()
        if not log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Audit log not found"
            )
        return log

    @staticmethod
    async def get_user_log_by_actor_id(
        db: AsyncSession, actor_id: UUID
    ) -> AuditLogResponse:
        """
        Fetch a single audit log by its ID.
        Args:
            db: Database session
            log_id: UUID of the log
        Returns:
            AuditLog instance
        Raises:
            HTTPException if not found
        """
        stmt = select(AuditLog).where(AuditLog.actor_id == actor_id)
        result = await db.execute(stmt)
        logs = result.scalars().all()

        return logs


class TransactionLogService:
    @staticmethod
    async def create_log(
        db: AsyncSession,
        *,
        vendor_id: UUID,
        amount: float,
        action: TransactionLogAction,
        status: TransactionLogStatus,
        details: Optional[dict] = None,
    ) -> TransactiontLogs:
        """
        Create a transaction log entry.
        """
        log = TransactiontLogs(
            vendor_id=vendor_id,
            amount=amount,
            action=action,
            status=status,
            details=details or {},
        )
        db.add(log)
        await db.flush()
        return log

    @staticmethod
    async def get_logs(
        db: AsyncSession,
        *,
        vendor_id: Optional[UUID] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[TransactiontLogs]:
        """
        Query transaction logs with optional filters.
        """
        stmt = select(TransactiontLogs)
        conditions = []
        if vendor_id:
            conditions.append(TransactiontLogs.vendor_id == vendor_id)
        if start_time:
            conditions.append(TransactiontLogs.timestamp >= start_time)
        if end_time:
            conditions.append(TransactiontLogs.timestamp <= end_time)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(TransactiontLogs.timestamp.desc()).offset(offset).limit(limit)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_log_by_id(db: AsyncSession, log_id: UUID) -> TransactiontLogs:
        stmt = select(TransactiontLogs).where(TransactiontLogs.id == log_id)
        result = await db.execute(stmt)
        log = result.scalar_one_or_none()
        if not log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Transaction log not found"
            )
        return log
