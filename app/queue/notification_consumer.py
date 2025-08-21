from typing import Dict, Any

from app.queue.base_consumer import BaseQueueConsumer

from app.utils.logger_config import setup_logger
from app.utils.utils import send_push_notification

logger = setup_logger()


class NotificationQueueConsumer(BaseQueueConsumer):
    def __init__(self):
        super().__init__("notification", "notification_updates")
        self._operation_handlers = {
            "send_notification": self.process_send_notification,
            # "schedule_notification": self.process_schedule_notification,
        }

    async def process_send_notification(self, payload: Dict[str, Any]):
        """Process sending a push notification"""
        try:
            tokens = payload.get("tokens", [])
            title = payload.get("title")
            message = payload.get("message")
           
            if not tokens or not title or not message:
                raise ValueError("Missing required notification fields")

            # Simulate sending push notification (replace with your actual push notification service)
            logger.info(f"Sending notification to {tokens}: {title} - {message}")
            
            # Example: await push_notification_service.send(tokens, title, message, navigate_to)

        except Exception as e:
            logger.error(f"Notification send error: {str(e)}")
            raise


