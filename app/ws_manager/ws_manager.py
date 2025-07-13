from fastapi import WebSocket
from typing import Dict, Set
import json



class ConnectionManager:
    def __init__(self):
        # Store active connections by client type
        self.active_connections: Dict[str, Set[WebSocket]] = {
            "admin": set(),
            "mobile": set()
        }
        # Store subscriptions for each connection
        self.connection_subscriptions: Dict[WebSocket, Set[str]] = {}

    async def connect(self, websocket: WebSocket, client_type: str = "admin"):
        """Accept a new WebSocket connection"""
        await websocket.accept()
        self.active_connections[client_type].add(websocket)
        self.connection_subscriptions[websocket] = set()
        print(f"Client connected. Total {client_type} connections: {len(self.active_connections[client_type])}")

    def disconnect(self, websocket: WebSocket, client_type: str = "admin"):
        """Remove a WebSocket connection"""
        self.active_connections[client_type].discard(websocket)
        if websocket in self.connection_subscriptions:
            del self.connection_subscriptions[websocket]
        print(f"Client disconnected. Total {client_type} connections: {len(self.active_connections[client_type])}")

    async def subscribe(self, websocket: WebSocket, event: str):
        """Subscribe a connection to specific events"""
        if websocket in self.connection_subscriptions:
            self.connection_subscriptions[websocket].add(event)
            print(f"Subscribed to event: {event}")

    async def unsubscribe(self, websocket: WebSocket, event: str):
        """Unsubscribe a connection from specific events"""
        if websocket in self.connection_subscriptions:
            self.connection_subscriptions[websocket].discard(event)
            print(f"Unsubscribed from event: {event}")

    async def broadcast_to_admins(self, message: dict):
        """Broadcast message to all admin connections"""
        disconnected = set()
        
        for connection in self.active_connections["admin"]:
            try:
                # Check if connection is subscribed to this event type
                if (connection in self.connection_subscriptions and 
                    message.get("type") in self.connection_subscriptions[connection]):
                    await connection.send_text(json.dumps(message))
            except Exception as e:
                print(f"Error sending message to admin: {e}")
                disconnected.add(connection)
        
        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection, "admin")

    async def broadcast_to_mobile(self, message: dict):
        """Broadcast message to all mobile connections"""
        disconnected = set()
        
        for connection in self.active_connections["mobile"]:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                print(f"Error sending message to mobile: {e}")
                disconnected.add(connection)
        
        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection, "mobile")

# Global manager instance
manager = ConnectionManager()
