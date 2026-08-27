from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class RoleResponse(BaseModel):
    id: int
    name: str


class TokenDataResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    is_active: bool
    role: RoleResponse


class LoginResponse(BaseModel):
    id: int
    email: EmailStr
    is_active: bool
    role: RoleResponse
    token: TokenDataResponse


# Forgot Password
class ForgotPasswordRequest(BaseModel):
    email: str


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=15)


class ResetPasswordResponse(BaseModel):
    message: str