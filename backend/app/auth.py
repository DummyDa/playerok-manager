from __future__ import annotations
import hashlib
import hmac
import secrets
import time
from fastapi import HTTPException, Request
from .db import Database, Login

def password_hash(password):
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex()+":"+value.hex()

def password_valid(password, hashed):
    try:
        salt, expected = hashed.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except Exception: return False

def session(db: Database, request: Request, required=True):
    token = request.cookies.get("manager_session", "")
    with db.sessions() as s:
        login = s.get(Login, hashlib.sha256(token.encode()).hexdigest()) if token else None
        if login and login.expires > time.time(): return login
    if required: raise HTTPException(401, "Войдите в панель")

def create_session(db):
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    with db.tx() as s:
        s.add(Login(digest=hashlib.sha256(token.encode()).hexdigest(), csrf=csrf, expires=time.time()+86400))
    return token, csrf
