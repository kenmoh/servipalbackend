from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.security import HTTPBearer
from jose import jwt, JWTError
from app.ws_manager.ws_manager import manager
import json
from datetime import datetime
from app.config.config import settings

router = APIRouter(prefix="/ws", tags=["websocket"])
security = HTTPBearer()

@router.websocket("")
async def websocket_endpoint(
    websocket: WebSocket,
    client_type: str = Query("admin"),
    token: str = Query(...)
):
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub") 
        if not user_id:
            await websocket.close()
            return
    except JWTError:
        await websocket.close()
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
                    await websocket.send_text(json.dumps({
                        "type": "subscription_confirmed",
                        "event": event,
                        "timestamp": datetime.now().isoformat()
                    }))
            
            elif message.get("type") == "unsubscribe":
                event = message.get("event")
                if event:
                    await manager.unsubscribe(websocket, event)
                    await websocket.send_text(json.dumps({
                        "type": "unsubscription_confirmed",
                        "event": event,
                        "timestamp": datetime.now().isoformat()
                    }))
            
            elif message.get("type") == "ping":
                # Handle ping/pong for connection health
                await websocket.send_text(json.dumps({
                    "type": "pong",
                    "timestamp": datetime.now().isoformat()
                }))
            
            else:
                # Echo back unknown message types
                await websocket.send_text(json.dumps({
                    "type": "echo",
                    "message": message,
                    "timestamp": datetime.now().isoformat()
                }))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, client_type, user_id)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket, client_type, user_id)
