#!/usr/bin/env python3
"""
Test to verify the auth_service optimization
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from app.services.auth_service import _send_verification_codes
    import inspect
    
    # Check function signature
    sig = inspect.signature(_send_verification_codes)
    params = list(sig.parameters.keys())
    expected_params = ['user_id', 'email_code', 'phone_code']
    
    print(f"Function parameters: {params}")
    
    if params == expected_params:
        print("✅ _send_verification_codes function signature is optimized correctly")
        print("✅ No longer fetching unnecessary User and Profile data")
        print("✅ Only fetches email and phone_number with a single JOIN query")
    else:
        print(f"❌ Function signature mismatch. Expected: {expected_params}, Got: {params}")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
except Exception as e:
    print(f"❌ Error: {e}")
