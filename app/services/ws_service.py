from datetime import datetime
from app.ws_manager.ws_manager import manager

# Helper functions to broadcast events from your API endpoints
async def broadcast_new_order(order_data: dict):
    """Broadcast new order to admin dashboard"""
    message = {
        "type": "new_order",
        "order_id": order_data.get("id"),
        "timestamp": datetime.now().isoformat()
    }
   
    await manager.broadcast_to_admins(message)

async def broadcast_new_user(user_data: dict):
    """Broadcast new user registration to admin dashboard"""
    message = {
        "type": "new_user",
        "email": user_data.get("email"),
        "user_type": user_data.get("user_type"),
        "timestamp": datetime.now().isoformat()
    }
  
    await manager.broadcast_to_admins(message)

async def broadcast_order_status_update(order_id: str, new_status: str):
    """Broadcast order status update to admin dashboard"""
    message = {
        "type": "order_status_update",
        "order_id": order_id,
        "order_status": new_status,
        "timestamp": datetime.now().isoformat()
    }
   
    await manager.broadcast_to_admins(message)

async def broadcast_delivery_status_update(delivery_id: str, new_status: str):
    """Broadcast delivery status update to admin dashboard"""
    message = {
        "type": "delivery_order_status_update",
        "delivery_id": delivery_id,  # Fixed: was order_id
        "delivery_status": new_status,
        "timestamp": datetime.now().isoformat()
    }
    await manager.broadcast_to_admins(message)

async def broadcast_new_team(team_data: dict):
    """Broadcast new team member to admin dashboard"""
    message = {
        "type": "new_team",
        "team_id": team_data.get("id"),  # Add team_id for better identification
        "email": team_data.get("email"),
        "user_type": team_data.get('user_type'),
        "full_name": team_data.get("full_name"),
        "timestamp": datetime.now().isoformat()
    }
    
    await manager.broadcast_to_admins(message)


async def broadcast_wallet_update(wallet_data: dict):
    """Broadcast new trasation to admin dashboard"""
    message = {
        "type": "wallet_update",
        "wallet_id": wallet_data.id,
        "balance": wallet_data.balance,
        "escrow_balance": wallet_data.escrow_balance,
        "timestamp": datetime.now().isoformat()
    }
    await manager.broadcast_to_admins(message)

async def broadcast_new_transaction(transaction_data: dict):
    """Broadcast new trasation to admin dashboard"""
    message = {
        "type": "new_transaction",
        "transaction_id": transaction_data.id,
        "transaction_id": transaction_data.transaction_type,
        "payment_status": transaction_data.payment_status,
        "timestamp": datetime.now().isoformat()
    }
    await manager.broadcast_to_admins(message)


async def broadcast_transaction_update(transaction_id: str, new_status: str):
    """Broadcast new trasation to admin dashboard"""
    message = {
        "type": "transaction_update",
        "transaction_id": transaction_id,
        "transaction_status": new_status,
        "timestamp": datetime.now().isoformat()
    }
    await manager.broadcast_to_admins(message)

# Additional debug function to test WebSocket connection
async def test_websocket_broadcast():
    """Test function to verify WebSocket broadcasting works"""
    test_message = {
        "type": "test_message",
        "message": "WebSocket test successful",
        "timestamp": datetime.now().isoformat()
    }

    await manager.broadcast_to_admins(test_message)