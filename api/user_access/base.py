from pydantic import BaseModel, EmailStr

class CreateUserRequest(BaseModel):
    email: EmailStr

class RoleResponse(BaseModel):
    id: int
    name: str


class CreateUserResponse(BaseModel):
    id: int
    email: EmailStr
    is_active: bool
    role: RoleResponse