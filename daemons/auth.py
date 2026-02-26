"""
Authentication module - PIN-based keypad auth with rate limiting.
"""

import hashlib
import time
from functools import wraps
from flask import session, redirect, url_for, request

# Rate limiting storage (in-memory, resets on restart)
_failed_attempts = {}  # ip -> {"count": int, "lockout_until": timestamp}

# Settings
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutes
SESSION_LIFETIME_HOURS = 24


def hash_pin(pin: str) -> str:
    """Hash a PIN for secure storage."""
    return hashlib.sha256(pin.encode()).hexdigest()


def verify_pin(entered_pin: str, stored_hash: str) -> bool:
    """Verify entered PIN against stored hash."""
    return hash_pin(entered_pin) == stored_hash


def get_client_ip() -> str:
    """Get client IP address."""
    return request.remote_addr or "unknown"


def is_locked_out() -> tuple[bool, int]:
    """
    Check if client IP is locked out.
    Returns (is_locked, seconds_remaining).
    """
    ip = get_client_ip()
    if ip not in _failed_attempts:
        return False, 0

    record = _failed_attempts[ip]
    if record["lockout_until"] and time.time() < record["lockout_until"]:
        remaining = int(record["lockout_until"] - time.time())
        return True, remaining

    return False, 0


def record_failed_attempt() -> tuple[int, bool]:
    """
    Record a failed login attempt.
    Returns (attempts_remaining, is_now_locked).
    """
    ip = get_client_ip()

    if ip not in _failed_attempts:
        _failed_attempts[ip] = {"count": 0, "lockout_until": None}

    _failed_attempts[ip]["count"] += 1
    count = _failed_attempts[ip]["count"]

    if count >= MAX_ATTEMPTS:
        _failed_attempts[ip]["lockout_until"] = time.time() + LOCKOUT_SECONDS
        return 0, True

    return MAX_ATTEMPTS - count, False


def clear_failed_attempts():
    """Clear failed attempts for current IP after successful login."""
    ip = get_client_ip()
    if ip in _failed_attempts:
        del _failed_attempts[ip]


def login_required(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function
