from fastapi import WebSocket
from typing import Dict, Set
import json


class ConnectionManager:
    def __init__(self):
        # Store active connections by client type
        self.active_connections: Dict[str, Set[WebSocket]] = {
            "admin": set(),
            "mobile": set(),
        }
        # Store subscriptions for each connection
        self.connection_subscriptions: Dict[WebSocket, Set[str]] = {}
        # Map user_id to WebSocket(s)
        self.user_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(
        self, websocket: WebSocket, client_type: str = "admin", user_id: str = None
    ):
        """Accept a new WebSocket connection. Optionally register user_id for personal messaging."""
        await websocket.accept()
        self.active_connections[client_type].add(websocket)
        self.connection_subscriptions[websocket] = set()
        if user_id:
            if user_id not in self.user_connections:
                self.user_connections[user_id] = set()
            self.user_connections[user_id].add(websocket)
        print(
            f"Client connected. Total {client_type} connections: {len(self.active_connections[client_type])}"
        )

    def disconnect(
        self, websocket: WebSocket, client_type: str = "admin", user_id: str = None
    ):
        """Remove a WebSocket connection. Optionally remove from user mapping."""
        self.active_connections[client_type].discard(websocket)
        if websocket in self.connection_subscriptions:
            del self.connection_subscriptions[websocket]
        if user_id and user_id in self.user_connections:
            self.user_connections[user_id].discard(websocket)
            if not self.user_connections[user_id]:
                del self.user_connections[user_id]
        print(
            f"Client disconnected. Total {client_type} connections: {len(self.active_connections[client_type])}"
        )

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
                if (
                    connection in self.connection_subscriptions
                    and message.get("type") in self.connection_subscriptions[connection]
                ):
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

    async def send_personal_message(self, message: dict, user_id: str):
        """Send a message to all WebSocket connections for a specific user_id."""
        if user_id not in self.user_connections:
            return
        disconnected = set()
        for connection in self.user_connections[user_id]:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                print(f"Error sending personal message to user {user_id}: {e}")
                disconnected.add(connection)
        # Clean up disconnected clients
        for connection in disconnected:
            self.user_connections[user_id].discard(connection)
        if user_id in self.user_connections and not self.user_connections[user_id]:
            del self.user_connections[user_id]


# Global manager instance
manager = ConnectionManager()
