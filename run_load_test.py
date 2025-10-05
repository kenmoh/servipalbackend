#!/usr/bin/env python3
"""
Load testing runner script
"""
import subprocess
import sys
import argparse
from load_test_config import SCENARIOS, HOST, WEB_HOST, WEB_PORT


def run_locust(scenario="medium_load", headless=False):
    """Run Locust load test"""
    config = SCENARIOS.get(scenario, SCENARIOS["medium_load"])
    
    cmd = [
        "locust",
        "-f", "locustfile.py",
        "--host", HOST,
        "--web-host", WEB_HOST,
        "--web-port", str(WEB_PORT),
    ]
    
    if headless:
        cmd.extend([
            "--headless",
            "-u", str(config["users"]),
            "-r", str(config["spawn_rate"]),
            "-t", config["run_time"]
        ])
    
    print(f"Running load test with scenario: {scenario}")
    print(f"Command: {' '.join(cmd)}")
    
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nLoad test interrupted by user")
    except subprocess.CalledProcessError as e:
        print(f"Load test failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run load tests")
    parser.add_argument(
        "--scenario", 
        choices=list(SCENARIOS.keys()), 
        default="medium_load",
        help="Load test scenario"
    )
    parser.add_argument(
        "--headless", 
        action="store_true",
        help="Run in headless mode (no web UI)"
    )
    
    args = parser.parse_args()
    run_locust(args.scenario, args.headless)
