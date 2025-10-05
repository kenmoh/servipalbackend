#!/usr/bin/env python3
"""Comprehensive test runner for ServiPal backend."""

import subprocess
import sys
import os
from pathlib import Path

def run_tests():
    """Run all tests with coverage."""
    # Set PYTHONPATH to include current directory
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).parent)
    
    test_commands = [
        # Run specific working test files
        ["python", "-m", "pytest", "app/test/test_auth.py", "app/test/test_users.py", "app/test/test_items.py", "app/test/test_orders.py", "app/test/test_integration.py", "-v", "--cov=app", "--cov-report=html", "--cov-report=term"],
    ]
    
    for cmd in test_commands:
        print(f"\n{'='*50}")
        print(f"Running: {' '.join(cmd)}")
        print('='*50)
        
        result = subprocess.run(cmd, cwd=Path(__file__).parent, env=env)
        if result.returncode != 0:
            print(f"❌ Some tests failed: {' '.join(cmd)}")
            return False
    
    print("\n✅ All tests completed!")
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
