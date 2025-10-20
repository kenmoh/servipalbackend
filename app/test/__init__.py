"""Initialize test environment"""
import os
from dotenv import load_dotenv

# Load test environment variables
load_dotenv(".env.test", override=True)
os.environ["TEST"] = "true"
