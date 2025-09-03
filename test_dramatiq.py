#!/usr/bin/env python3
"""
Simple test script to verify Dramatiq setup
"""
import os
import sys

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.utils.s3_service import process_video_to_gif_background

def test_dramatiq_setup():
    """Test that Dramatiq can queue a task"""
    try:
        print("Testing Dramatiq setup...")
        
        # Try to queue a test task
        task = process_video_to_gif_background.send("test_video.mp4", "test.gif", "http://example.com/test.mp4")
        
        print(f"✅ Dramatiq task queued successfully!")
        print(f"Task ID: {task.id}")
        print(f"Task status: {task.status}")
        
        return True
        
    except Exception as e:
        print(f"❌ Dramatiq setup failed: {e}")
        return False

if __name__ == "__main__":
    test_dramatiq_setup() 