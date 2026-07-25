from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.jwt import decode_access_token
from app.database.session import get_db
from sqlalchemy import select
from app.models import User
from app.config.logging_config import get_logger

_log = get_logger("flavorag.auth")
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        _log.warning("auth_no_token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证Token")

    payload = decode_access_token(credentials.credentials)
    if not payload:
        _log.warning("auth_token_invalid", token_preview=credentials.credentials[:10] + "...")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token无效或已过期")

    user_id: str = payload.get("sub")
    username: str = payload.get("username", "?")
    if not user_id:
        _log.warning("auth_token_malformed", username=username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token格式错误")

    result = await db.execute(select(User).where(User.id == user_id, User.deleted == 0))
    user = result.scalar_one_or_none()
    if not user:
        _log.warning("auth_user_not_found", user_id=user_id, username=username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        _log.warning("auth_insufficient_permission", username=user.username, role=user.role, required="admin")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user
