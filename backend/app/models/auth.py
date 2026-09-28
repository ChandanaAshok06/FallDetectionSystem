from pydantic import BaseModel, EmailStr

class RegisterRequest(BaseModel):
    name: str
    caregiver_email: EmailStr
    password: str

class LoginRequest(BaseModel):
    caregiver_email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"