import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database.session import get_db
from app.models import User
from app.auth.jwt import hash_password, verify_password, create_access_token
from app.auth.dependencies import get_current_user
from app.config.logging_config import get_logger

_log = get_logger("flavorag.auth")

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
    t0 = time.time()
    _log.info("login_attempt", username=req.username)

    try:
        result = await db.execute(
            select(User).where(User.username == req.username, User.deleted == 0)
        )
        user = result.scalar_one_or_none()
        lookup_ms = int((time.time() - t0) * 1000)
        _log.info("user_lookup", username=req.username, found=(user is not None), took_ms=lookup_ms)

        if not user:
            _log.warning("login_failed_user_not_found", username=req.username, took_ms=lookup_ms)
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        t_verify = time.time()
        pwd_ok = verify_password(req.password, user.password)
        verify_ms = int((time.time() - t_verify) * 1000)
        _log.info("password_verify", username=req.username, success=pwd_ok, took_ms=verify_ms)

        if not pwd_ok:
            _log.warning("login_failed_bad_password", username=req.username, took_ms=int((time.time() - t0) * 1000))
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        t_token = time.time()
        token = create_access_token(user.id, user.username, user.role)
        token_ms = int((time.time() - t_token) * 1000)
        total_ms = int((time.time() - t0) * 1000)
        _log.info("login_success", username=user.username, role=user.role, token_create_ms=token_ms, total_ms=total_ms)
        return TokenResponse(token=token, username=user.username, role=user.role)

    except HTTPException:
        raise
    except Exception as exc:
        total_ms = int((time.time() - t0) * 1000)
        _log.error("login_error", username=req.username, error_type=type(exc).__name__, error=str(exc), total_ms=total_ms)
        raise


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    t0 = time.time()
    _log.info("register_attempt", username=req.username)

    try:
        existing = await db.execute(
            select(User).where(User.username == req.username)
        )
        if existing.scalar_one_or_none():
            _log.warning("register_failed_duplicate", username=req.username)
            raise HTTPException(status_code=409, detail="用户名已存在")

        user = User(
            username=req.username,
            password=hash_password(req.password),
            role="user",
        )
        db.add(user)
        await db.flush()

        token = create_access_token(user.id, user.username, user.role)
        total_ms = int((time.time() - t0) * 1000)
        _log.info("register_success", username=user.username, user_id=user.id, role=user.role, total_ms=total_ms)
        return TokenResponse(token=token, username=user.username, role=user.role)

    except HTTPException:
        raise
    except Exception as exc:
        total_ms = int((time.time() - t0) * 1000)
        _log.error("register_error", username=req.username, error_type=type(exc).__name__, error=str(exc), total_ms=total_ms)
        raise


@router.get("/current", response_model=UserInfo)
async def current_user(user: User = Depends(get_current_user)):
    _log.info("current_user", username=user.username, user_id=user.id, role=user.role)
    return UserInfo(id=user.id, username=user.username, role=user.role, avatar=user.avatar)
