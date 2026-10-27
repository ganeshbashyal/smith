from fastapi import Header, HTTPException
from .config import Settings


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if x_admin_token != Settings().admin_token:
        raise HTTPException(status_code=401, detail="Valid X-Admin-Token required")
