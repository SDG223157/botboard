import time
import jwt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.config import settings

serializer = URLSafeTimedSerializer(settings.SECRET_KEY)

# Magic link tokens (signed email)

def generate_magic_link(email: str) -> str:
    return serializer.dumps(email)

def verify_magic_link(token: str, max_age: int | None = None) -> str:
    if max_age is None:
        max_age = settings.MAGIC_LINK_EXP_MIN * 60
    try:
        email = serializer.loads(token, max_age=max_age)
        return email
    except SignatureExpired as e:
        raise ValueError("Magic link expired") from e
    except BadSignature as e:
        raise ValueError("Invalid magic link") from e

# Access tokens (JWT)

def generate_access_token(sub: str) -> str:
    exp = int(time.time()) + settings.ACCESS_TOKEN_EXP_MIN * 60
    payload = {"sub": sub, "exp": exp}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

def verify_access_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])  # raises on error
