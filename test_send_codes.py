import asyncio
from app.services.auth_service import _send_verification_codes

async def main():
    await _send_verification_codes(
        email="kenneth.aremoh@gmail.com",
        phone_number="2347063692766",
        email_code="3456",
        phone_code="9876"
    )

if __name__ == "__main__":
    asyncio.run(main())
