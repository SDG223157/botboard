import aiosmtplib
from email.message import EmailMessage
from app.config import settings

async def send_email(to: str, subject: str, html: str):
    if not settings.SMTP_HOST:
        raise RuntimeError("SMTP not configured")
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(html, subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        start_tls=settings.SMTP_TLS,
        username=settings.SMTP_USERNAME,
        password=settings.SMTP_PASSWORD,
    )
