from pydantic import BaseModel, EmailStr

class CreateUserRequest(BaseModel):
    email: EmailStr

class CreateUserResponse(BaseModel):
    id: int
    email: EmailStr
    role: str
    temporary_password: str