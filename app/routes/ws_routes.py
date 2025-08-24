from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.security import HTTPBearer
from app.ws_manager.ws_manager import manager
from app.auth.auth import get_user_from_token
from app.database.database import async_session
import json
from datetime import datetime
from app.utils.logger_config import setup_logger


logger = setup_logger()

router = APIRouter(prefix="/ws", tags=["websocket"])
security = HTTPBearer()


# async def get_token_from_cookie(websocket: WebSocket):
#     """Extract HttpOnly cookie from WebSocket headers"""
#     try:
#         # Get cookie header from WebSocket headers
#         cookie_header = websocket.headers.get("cookie")

#         if not cookie_header:
#             logger.error(f"No cookies provided")
#             await websocket.close(code=1008, reason="No cookies provided")
#             return None

#         # Parse cookies manually
#         cookies = SimpleCookie()
#         cookies.load(cookie_header)

#         # Extract the access_token
#         if 'access_token' in cookies:
#             token = cookies['access_token'].value
#             return token
#         logger.error(f"Access token not found in cookies")
#         await websocket.close(code=1008, reason="Access token not found in cookies")
#         return None

#     except Exception as e:
#         logger.error(f"Cookie parsing error: {str(e)}")
#         await websocket.close(code=1011, reason=f"Cookie parsing error: {str(e)}")
#         return None


@router.websocket("")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    client_type: str = Query("admin"),
):
    """Main WebSocket endpoint for real-time communication"""
    async with async_session() as db:
        user = await get_user_from_token(token=token, db=db)
        if not user:
            await websocket.close(code=1008)
            return

        user_id = str(user.id)
        await manager.connect(websocket, client_type, user_id)

        try:
            while True:
                # Wait for messages from the client
                data = await websocket.receive_text()
                message = json.loads(data)

                # Handle different message types
                if message.get("type") == "subscribe":
                    event = message.get("event")
                    if event:
                        await manager.subscribe(websocket, event)
                        # Send confirmation
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "subscription_confirmed",
                                    "event": event,
                                    "timestamp": datetime.now().isoformat(),
                                }
                            )
                        )

                elif message.get("type") == "unsubscribe":
                    event = message.get("event")
                    if event:
                        await manager.unsubscribe(websocket, event)
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "unsubscription_confirmed",
                                    "event": event,
                                    "timestamp": datetime.now().isoformat(),
                                }
                            )
                        )

                elif message.get("type") == "ping":
                    # Handle ping/pong for connection health
                    await websocket.send_text(
                        json.dumps({"type": "pong", "timestamp": datetime.now().isoformat()})
                    )

                else:
                    # Echo back unknown message types
                    await websocket.send_text(
                        json.dumps({"type": "echo", "message": message, "timestamp": datetime.now().isoformat()})
                    )

        except WebSocketDisconnect:
            manager.disconnect(websocket, client_type, user_id)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            manager.disconnect(websocket, client_type, user_id)
