from pydantic import BaseModel, EmailStr

class MagicLinkRequest(BaseModel):
    email: EmailStr

class MagicLinkToken(BaseModel):
    token: str

class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
