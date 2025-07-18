from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from app.models.models import AuditLog
from fastapi import HTTPException, status

from app.schemas.audit_logs import AuditLogResponse



class AuditLogService:
    @staticmethod
    async def log_action(
        db: AsyncSession,
        *,
        actor_id: UUID,
        actor_name: str,
        actor_role: str,
        action: str,
        resource_type: str,
        resource_id: Optional[UUID] = None,
        resource_summary: Optional[str] = None,
        changes: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditLogResponse:
        """
        Log an action to the audit log.
        """
        try:
            log = AuditLog(
                timestamp=datetime.now(),
                actor_id=actor_id,
                actor_name=actor_name,
                actor_role=actor_role,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                resource_summary=resource_summary,
                changes=changes,
                ip_address=ip_address,
                extra_metadata=extra_metadata,
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)
            return log
        except Exception as e:
            await db.rollback()
            raise

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
    async def get_log_by_id(db: AsyncSession, log_id: UUID) -> AuditLogResponse:
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
        stmt = select(AuditLog).where(AuditLog.id == log_id)
        result = await db.execute(stmt)
        log = result.scalar_one_or_none()
        if not log:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit log not found")
        return log 