from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database.session import get_db
from app.models import User
from app.auth.jwt import hash_password, verify_password, create_access_token
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    username: str
    role: str


class UserInfo(BaseModel):
    id: str
    username: str
    role: str
    avatar: str | None = None


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.username == req.username, User.deleted == 0)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user.id, user.username, user.role)
    return TokenResponse(token=token, username=user.username, role=user.role)


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(User).where(User.username == req.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已存在")

    user = User(
        username=req.username,
        password=hash_password(req.password),
        role="user",
    )
    db.add(user)
    await db.flush()

    token = create_access_token(user.id, user.username, user.role)
    return TokenResponse(token=token, username=user.username, role=user.role)


@router.get("/current", response_model=UserInfo)
async def current_user(user: User = Depends(get_current_user)):
    return UserInfo(id=user.id, username=user.username, role=user.role, avatar=user.avatar)
