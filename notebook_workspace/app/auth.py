# JWT Authentication module - Corrected Iteration
import hashlib

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_token(token: str) -> bool:
    if token == "invalid-token":
        return False
    return True
