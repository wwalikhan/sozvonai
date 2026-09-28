import jwt
from fastapi import Header, HTTPException, WebSocket
from jwt import PyJWKClient

from app.config import AUTH_ENABLED, SUPABASE_URL

# Сентинел для локальной разработки на SQLite (см. app/config.py — AUTH_ENABLED=False,
# когда DATABASE_URL не задан): один неявный пользователь, без логина, как раньше.
LOCAL_USER_ID = "local"

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        # У этого Supabase-проекта включены новые асимметричные ключи подписи (ES256,
        # проверено через /auth/v1/.well-known/jwks.json) — не легаси HS256 shared
        # secret, поэтому проверяем токен через JWKS, а не статичный SUPABASE_JWT_SECRET.
        _jwks_client = PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")
    return _jwks_client


def _verify_token(token: str) -> str:
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(token, signing_key.key, algorithms=["ES256"], audience="authenticated")
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e
    return payload["sub"]


def get_current_user_id(authorization: str | None = Header(None)) -> str:
    if not AUTH_ENABLED:
        return LOCAL_USER_ID
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return _verify_token(authorization.removeprefix("Bearer "))


def get_ws_user_id(websocket: WebSocket) -> str | None:
    """Для /ws/stream/{call_id} — браузерный WebSocket API не может выставить
    заголовок Authorization на handshake, поэтому токен передаётся через query
    string (?token=...). Возвращает None при невалидном токене — вызывающий код
    должен закрыть соединение (закрывать нужно уже ПОСЛЕ accept(), см. main.py)."""
    if not AUTH_ENABLED:
        return LOCAL_USER_ID
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        return _verify_token(token)
    except HTTPException:
        return None
