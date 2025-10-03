#!/usr/bin/env python3
"""
Test runner script for ServiPal backend.
Provides different test execution options.
"""

import subprocess
import sys
import argparse


def run_command(cmd):
    """Run a command and return the result."""
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Run tests for ServiPal backend")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument("--integration", action="store_true", help="Run integration tests only")
    parser.add_argument("--auth", action="store_true", help="Run auth tests only")
    parser.add_argument("--orders", action="store_true", help="Run order tests only")
    parser.add_argument("--users", action="store_true", help="Run user tests only")
    parser.add_argument("--items", action="store_true", help="Run item tests only")
    parser.add_argument("--coverage", action="store_true", help="Run with coverage report")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--file", help="Run specific test file")
    
    args = parser.parse_args()
    
    # Base pytest command
    cmd = ["python", "-m", "pytest"]
    
    # Add verbosity
    if args.verbose:
        cmd.append("-v")
    
    # Add coverage if requested
    if args.coverage:
        cmd.extend(["--cov=app", "--cov-report=term-missing", "--cov-report=html"])
    
    # Add specific test markers
    if args.unit:
        cmd.extend(["-m", "unit"])
    elif args.integration:
        cmd.extend(["-m", "integration"])
    elif args.auth:
        cmd.extend(["-m", "auth"])
    elif args.orders:
        cmd.extend(["-m", "orders"])
    elif args.users:
        cmd.extend(["-m", "users"])
    elif args.items:
        cmd.extend(["-m", "items"])
    
    # Add specific file if provided
    if args.file:
        cmd.append(f"app/test/{args.file}")
    
    # Run the tests
    success = run_command(cmd)
    
    if not success:
        print("Tests failed!")
        sys.exit(1)
    else:
        print("All tests passed!")


if __name__ == "__main__":
    main()
