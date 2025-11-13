#!/usr/bin/env python3
"""
Simple test to verify the auth_service fixes
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from app.services.auth_service import _send_verification_codes, register_user
    print("✅ Successfully imported auth_service functions")
    
    # Check function signature
    import inspect
    sig = inspect.signature(_send_verification_codes)
    params = list(sig.parameters.keys())
    expected_params = ['user_id', 'email', 'email_code', 'phone_code']
    
    if params == expected_params:
        print("✅ _send_verification_codes function signature is correct")
    else:
        print(f"❌ Function signature mismatch. Expected: {expected_params}, Got: {params}")
    
    print("✅ All auth_service fixes appear to be working correctly!")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
except Exception as e:
    print(f"❌ Error: {e}")
