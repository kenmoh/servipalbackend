from pathlib import Path
from fastapi.templating import Jinja2Templates

# Define the base directory of the 'app' module
APP_DIR = Path(__file__).resolve().parent

# Single, reusable templates instance that can be imported anywhere
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
