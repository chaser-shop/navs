import os, base64, hashlib
from cryptography.fernet import Fernet
_f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(os.environ["SECRET_KEY"].encode()).digest()))
enc = lambda s: _f.encrypt(s.encode()).decode()
dec = lambda s: _f.decrypt(s.encode()).decode()
