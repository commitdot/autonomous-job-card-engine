# Test suite for auth.py
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.auth import hash_password, verify_token

def test_hash_password():
    assert hash_password("secret") == "2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b"

def test_verify_token():
    assert verify_token("valid-token") is True
    assert verify_token("invalid-token") is False

if __name__ == "__main__":
    test_hash_password()
    test_verify_token()
    print("All tests passed successfully!")
