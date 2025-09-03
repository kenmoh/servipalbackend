#!/usr/bin/env python3
"""
Script to run the Dramatiq worker for video processing
"""
import os
import sys

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

import dramatiq
from app.utils.s3_service import process_video_to_gif_background

if __name__ == "__main__":
    print("Starting Dramatiq worker for video processing...")
    print("Worker will process video to GIF conversion tasks in the background")
    print("Press Ctrl+C to stop the worker")
    
    try:
        # Start the worker
        dramatiq.cli.main()
    except KeyboardInterrupt:
        print("\nWorker stopped by user")
    except Exception as e:
        print(f"Worker error: {e}") 