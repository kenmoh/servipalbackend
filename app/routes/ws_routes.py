from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.security import HTTPBearer
from http.cookies import SimpleCookie
from jose import jwt, JWTError
from app.ws_manager.ws_manager import manager
import json
from datetime import datetime
from app.config.config import settings

router = APIRouter(prefix="/ws", tags=["websocket"])
security = HTTPBearer()

# async def get_token_from_cookie(websocket: WebSocket):
#     # Access the 'access_token' cookie from the WebSocket headers
#     cookies = websocket.cookies
#     token = cookies.get("access_token")
#     if not token:
#         await websocket.close(code=1008, reason="Policy violation: No token provided")
#         return None
#     # Optionally: validate the token here
#     return token

async def get_token_from_cookie(websocket: WebSocket):
    """Extract HttpOnly cookie from WebSocket headers"""
    try:
        # Get cookie header from WebSocket headers
        cookie_header = websocket.headers.get("cookie")
        
        if not cookie_header:
            await websocket.close(code=1008, reason="No cookies provided")
            return None
        
        # Parse cookies manually
        cookies = SimpleCookie()
        cookies.load(cookie_header)
        
        # Extract the access_token
        if 'access_token' in cookies:
            token = cookies['access_token'].value
            return token
        
        await websocket.close(code=1008, reason="Access token not found in cookies")
        return None
        
    except Exception as e:
        await websocket.close(code=1011, reason=f"Cookie parsing error: {str(e)}")
        return None


@router.websocket("")
async def websocket_endpoint(
    websocket: WebSocket, client_type: str = Query("admin")
):
    try:
        token = await get_token_from_cookie(websocket)
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=1008, reason="Invalid token: no user ID")
            return
    except JWTError:
        await websocket.close(code=1008, reason=reason=f"Invalid token: {str(e)}")
        return

    """Main WebSocket endpoint for real-time communication"""
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
                    json.dumps(
                        {"type": "pong", "timestamp": datetime.now().isoformat()}
                    )
                )

            else:
                # Echo back unknown message types
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "echo",
                            "message": message,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket, client_type, user_id)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket, client_type, user_id)
