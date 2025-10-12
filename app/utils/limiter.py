from slowapi import Limiter
from slowapi.util import get_remote_address
from app.config.config import settings


limiter = Limiter(key_func=get_remote_address, enabled=not settings.TEST)
