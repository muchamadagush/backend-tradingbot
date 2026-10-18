# File: backend/auth.py
"""
Authentication and Authorization Module
Supports API Key and JWT authentication with rate limiting
"""

import os
import time
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
from enum import Enum
from functools import wraps
from collections import defaultdict

from fastapi import HTTPException, Security, Depends, Request, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import jwt

# ==========================================
# CONFIGURATION
# ==========================================

# Load from environment
API_KEY_SECRET = os.getenv("API_KEY_SECRET")
JWT_SECRET = os.getenv("JWT_SECRET")
ENV_MODE = os.getenv("ENV_MODE", "development").lower()

if not API_KEY_SECRET or not JWT_SECRET:
    if ENV_MODE == "production":
        raise RuntimeError("API_KEY_SECRET and JWT_SECRET environment variables must be set in production.")
    else:
        # Development fallback with warning
        API_KEY_SECRET = API_KEY_SECRET or "dev-api-key-secret-salt-do-not-use-in-production"
        JWT_SECRET = JWT_SECRET or "dev-jwt-secret-key-32-chars-long-do-not-use-prod"
        print("⚠️ [AUTH] Running with fallback development secrets. Set API_KEY_SECRET and JWT_SECRET for production.")
JWT_ALGORITHM = "HS256"

JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "24"))

# Auth mode: "disabled", "api_key", "jwt", "both"
AUTH_MODE = os.getenv("AUTH_MODE", "api_key").lower()

# Default API keys (comma-separated in env)
# Format: key1:role1,key2:role2
# Roles: admin, trader, readonly
DEFAULT_API_KEYS = os.getenv("API_KEYS", "")


class UserRole(str, Enum):
    ADMIN = "admin"      # Full access
    TRADER = "trader"    # Can trade, no admin functions
    READONLY = "readonly"  # View only


class AuthUser(BaseModel):
    """Authenticated user info"""
    user_id: str
    role: UserRole
    api_key_hash: Optional[str] = None
    

# ==========================================
# API KEY STORE
# ==========================================

class APIKeyStore:
    """
    In-memory API key store.
    In production, use database or secrets manager.
    """
    
    def __init__(self):
        self._keys: Dict[str, Tuple[str, UserRole]] = {}  # hash -> (user_id, role)
        self._load_default_keys()
    
    def _load_default_keys(self):
        """Load API keys from environment."""
        if not DEFAULT_API_KEYS:
            # Generate a default admin key if none provided
            default_key = os.getenv("BOT_API_KEY", "")
            if default_key:
                self.add_key(default_key, "default_admin", UserRole.ADMIN)
            return
        
        for entry in DEFAULT_API_KEYS.split(","):
            entry = entry.strip()
            if ":" in entry:
                key, role_str = entry.split(":", 1)
                role = UserRole(role_str.lower()) if role_str.lower() in [r.value for r in UserRole] else UserRole.READONLY
                self.add_key(key.strip(), f"user_{len(self._keys)}", role)
            else:
                self.add_key(entry, f"user_{len(self._keys)}", UserRole.TRADER)
    
    def _hash_key(self, api_key: str) -> str:
        """Hash API key for storage."""
        return hashlib.sha256(api_key.encode()).hexdigest()
    
    def add_key(self, api_key: str, user_id: str, role: UserRole):
        """Add or update an API key."""
        key_hash = self._hash_key(api_key)
        self._keys[key_hash] = (user_id, role)
    
    def validate_key(self, api_key: str) -> Optional[AuthUser]:
        """Validate API key and return user info."""
        key_hash = self._hash_key(api_key)
        if key_hash in self._keys:
            user_id, role = self._keys[key_hash]
            return AuthUser(user_id=user_id, role=role, api_key_hash=key_hash)
        return None
    
    def revoke_key(self, api_key: str) -> bool:
        """Revoke an API key."""
        key_hash = self._hash_key(api_key)
        if key_hash in self._keys:
            del self._keys[key_hash]
            return True
        return False
    
    def generate_key(self, user_id: str, role: UserRole) -> str:
        """Generate a new API key for a user."""
        api_key = secrets.token_urlsafe(32)
        self.add_key(api_key, user_id, role)
        return api_key
    
    def list_users(self) -> list:
        """List all registered users (without exposing keys)."""
        return [
            {"user_id": user_id, "role": role.value, "key_prefix": key_hash[:8] + "..."}
            for key_hash, (user_id, role) in self._keys.items()
        ]
    
    def revoke_by_user_id(self, user_id: str) -> int:
        """Revoke all keys for a user. Returns count of revoked keys."""
        to_remove = [h for h, (uid, _) in self._keys.items() if uid == user_id]
        for h in to_remove:
            del self._keys[h]
        return len(to_remove)


# Global API key store
api_key_store = APIKeyStore()


# ==========================================
# RATE LIMITER
# ==========================================

class RateLimiter:
    """
    Token bucket rate limiter per API key/IP.
    """
    
    def __init__(self):
        # Rate limits per role (requests per minute)
        self.limits: Dict[UserRole, int] = {
            UserRole.ADMIN: 300,
            UserRole.TRADER: 120,
            UserRole.READONLY: 60,
        }
        self.default_limit = 30  # For unauthenticated (if allowed)
        
        # Buckets: {identifier: {"tokens": float, "last_update": float}}
        self._buckets: Dict[str, Dict] = defaultdict(lambda: {"tokens": 0, "last_update": time.time()})
        self._lock_time: Dict[str, float] = {}  # Lockout timestamps
    
    def _get_bucket(self, identifier: str, limit: int) -> Dict:
        """Get or create a bucket for identifier."""
        bucket = self._buckets[identifier]
        now = time.time()
        
        # Refill tokens based on time passed
        time_passed = now - bucket["last_update"]
        tokens_to_add = time_passed * (limit / 60.0)  # tokens per second
        bucket["tokens"] = min(limit, bucket["tokens"] + tokens_to_add)
        bucket["last_update"] = now
        
        return bucket
    
    def check_rate_limit(self, identifier: str, role: Optional[UserRole] = None) -> Tuple[bool, Dict]:
        """
        Check if request is within rate limit.
        Returns (allowed, info_dict).
        """
        # Check if locked out
        if identifier in self._lock_time:
            if time.time() < self._lock_time[identifier]:
                remaining = int(self._lock_time[identifier] - time.time())
                return False, {"locked": True, "retry_after": remaining}
            else:
                del self._lock_time[identifier]
        
        limit = self.limits.get(role, self.default_limit) if role else self.default_limit
        bucket = self._get_bucket(identifier, limit)
        
        if bucket["tokens"] >= 1:
            bucket["tokens"] -= 1
            return True, {
                "limit": limit,
                "remaining": int(bucket["tokens"]),
                "reset_in": int(60 - (time.time() - bucket["last_update"]) % 60)
            }
        
        # Rate limited - lock for a short period
        self._lock_time[identifier] = time.time() + 10  # 10 second lockout
        return False, {"limit": limit, "remaining": 0, "retry_after": 10}
    
    def get_identifier(self, request: Request, user: Optional[AuthUser] = None) -> str:
        """Get rate limit identifier from request."""
        if user and user.api_key_hash:
            return f"key:{user.api_key_hash[:16]}"
        # Fall back to IP
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        return f"ip:{request.client.host if request.client else 'unknown'}"


# Global rate limiter
rate_limiter = RateLimiter()


# ==========================================
# SECURITY SCHEMES
# ==========================================

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


# ==========================================
# JWT UTILITIES
# ==========================================

def create_jwt_token(user_id: str, role: UserRole, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT token."""
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=JWT_EXPIRY_HOURS))
    payload = {
        "sub": user_id,
        "role": role.value,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt_token(token: str) -> Optional[AuthUser]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return AuthUser(
            user_id=payload["sub"],
            role=UserRole(payload["role"]),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        return None


# ==========================================
# DEPENDENCY FUNCTIONS
# ==========================================

async def get_api_key(api_key: str = Security(api_key_header)) -> Optional[str]:
    """Extract API key from header."""
    return api_key


async def get_bearer_token(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)
) -> Optional[str]:
    """Extract Bearer token from header."""
    if credentials:
        return credentials.credentials
    return None


async def get_current_user(
    request: Request,
    api_key: Optional[str] = Depends(get_api_key),
    token: Optional[str] = Depends(get_bearer_token),
) -> Optional[AuthUser]:
    """
    Get current authenticated user from API key or JWT.
    Returns None if auth is disabled or no valid credentials.
    """
    if AUTH_MODE == "disabled":
        # Return a default admin user when auth is disabled
        return AuthUser(user_id="anonymous", role=UserRole.ADMIN)
    
    user = None
    
    # Try API key first
    if api_key and AUTH_MODE in ("api_key", "both"):
        user = api_key_store.validate_key(api_key)
    
    # Try JWT token
    if not user and token and AUTH_MODE in ("jwt", "both"):
        user = decode_jwt_token(token)
    
    return user


async def require_auth(
    request: Request,
    user: Optional[AuthUser] = Depends(get_current_user),
) -> AuthUser:
    """Require authentication - raises 401 if not authenticated."""
    if AUTH_MODE == "disabled":
        return AuthUser(user_id="anonymous", role=UserRole.ADMIN)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication",
            headers={"WWW-Authenticate": "Bearer, ApiKey"},
        )
    
    # Check rate limit
    identifier = rate_limiter.get_identifier(request, user)
    allowed, info = rate_limiter.check_rate_limit(identifier, user.role)
    
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Retry after {info.get('retry_after', 10)} seconds",
            headers={"Retry-After": str(info.get("retry_after", 10))},
        )
    
    return user


async def require_role(required_roles: list[UserRole]):
    """Factory for role-based access control."""
    async def check_role(user: AuthUser = Depends(require_auth)) -> AuthUser:
        if user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {[r.value for r in required_roles]}",
            )
        return user
    return check_role


# ==========================================
# PRE-BUILT DEPENDENCIES
# ==========================================

async def require_admin(user: AuthUser = Depends(require_auth)) -> AuthUser:
    """Require admin role."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_trader(user: AuthUser = Depends(require_auth)) -> AuthUser:
    """Require trader or admin role."""
    if user.role not in (UserRole.ADMIN, UserRole.TRADER):
        raise HTTPException(status_code=403, detail="Trader access required")
    return user


async def optional_auth(
    request: Request,
    user: Optional[AuthUser] = Depends(get_current_user),
) -> Optional[AuthUser]:
    """Optional authentication - for read-only endpoints."""
    if AUTH_MODE == "disabled":
        return AuthUser(user_id="anonymous", role=UserRole.ADMIN)
    
    # Still check rate limit for unauthenticated users
    if not user:
        identifier = rate_limiter.get_identifier(request)
        allowed, info = rate_limiter.check_rate_limit(identifier)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Retry after {info.get('retry_after', 10)} seconds",
            )
    
    return user


# ==========================================
# AUTH ENDPOINTS (for JWT login)
# ==========================================

class LoginRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str


def create_auth_router():
    """Create auth router with login endpoint."""
    from fastapi import APIRouter, Request
    from rate_limiter import limiter, RATE_LIMIT_AUTH, RATE_LIMIT_READONLY
    
    router = APIRouter(prefix="/api/auth", tags=["auth"])
    
    @router.post("/login", response_model=TokenResponse)
    @limiter.limit(RATE_LIMIT_AUTH)
    async def login(request: Request, req: LoginRequest):
        """Exchange API key for JWT token. Rate limited to 5/min to prevent brute force."""
        user = api_key_store.validate_key(req.api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        from fastapi.responses import JSONResponse
        token = create_jwt_token(user.user_id, user.role)
        response = TokenResponse(
            access_token=token,
            expires_in=JWT_EXPIRY_HOURS * 3600,
            role=user.role.value,
        )
        return JSONResponse(content=response.dict())
    
    @router.get("/me")
    @limiter.limit(RATE_LIMIT_READONLY)
    async def get_me(request: Request, user: AuthUser = Depends(require_auth)):
        """Get current user info."""
        return {
            "user_id": user.user_id,
            "role": user.role.value,
        }
    
    from fastapi.responses import JSONResponse
    @router.get("/status")
    @limiter.limit(RATE_LIMIT_READONLY)
    async def auth_status(request: Request):
        """Get auth configuration status (public)."""
        return JSONResponse(content={
            "auth_mode": AUTH_MODE,
            "auth_enabled": AUTH_MODE != "disabled",
            "jwt_enabled": AUTH_MODE in ("jwt", "both"),
            "api_key_enabled": AUTH_MODE in ("api_key", "both"),
        })
    
    # ==========================================
    # ADMIN API KEY MANAGEMENT
    # ==========================================
    
    class CreateUserRequest(BaseModel):
        user_id: str
        role: str = "trader"  # admin, trader, readonly
    
    class CreateUserResponse(BaseModel):
        user_id: str
        role: str
        api_key: str
        message: str
    
    @router.get("/users")
    async def list_users(user: AuthUser = Depends(require_admin)):
        """List all registered API key users. Admin only."""
        return {
            "users": api_key_store.list_users(),
            "total": len(api_key_store.list_users()),
        }
    
    @router.post("/users", response_model=CreateUserResponse)
    async def create_user(req: CreateUserRequest, user: AuthUser = Depends(require_admin)):
        """Create a new API key user. Admin only."""
        # Validate role
        try:
            role = UserRole(req.role.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {[r.value for r in UserRole]}")
        
        # Generate API key
        api_key = api_key_store.generate_key(req.user_id, role)
        
        return CreateUserResponse(
            user_id=req.user_id,
            role=role.value,
            api_key=api_key,
            message="API key created successfully. Store it securely - it cannot be retrieved again.",
        )
    
    @router.delete("/users/{user_id}")
    async def delete_user(user_id: str, user: AuthUser = Depends(require_admin)):
        """Revoke all API keys for a user. Admin only."""
        if user_id == user.user_id:
            raise HTTPException(status_code=400, detail="Cannot delete your own user")
        
        count = api_key_store.revoke_by_user_id(user_id)
        if count == 0:
            raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
        
        return {
            "status": "deleted",
            "user_id": user_id,
            "keys_revoked": count,
        }
    
    @router.post("/users/{user_id}/regenerate")
    async def regenerate_key(user_id: str, user: AuthUser = Depends(require_admin)):
        """Regenerate API key for a user (revokes old key). Admin only."""
        # Find user's current role
        current_users = api_key_store.list_users()
        user_info = next((u for u in current_users if u["user_id"] == user_id), None)
        
        if not user_info:
            raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
        
        role = UserRole(user_info["role"])
        
        # Revoke old keys
        api_key_store.revoke_by_user_id(user_id)
        
        # Generate new key
        new_key = api_key_store.generate_key(user_id, role)
        
        return {
            "user_id": user_id,
            "role": role.value,
            "api_key": new_key,
            "message": "New API key generated. Old key(s) revoked.",
        }
    
    return router


# Export auth router
auth_router = create_auth_router()
