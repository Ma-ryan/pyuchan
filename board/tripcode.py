import base64
import hashlib
import hmac
import re
from django.conf import settings

# Python 3.13 removed the standard library crypt module (PEP 594)
try:
    import crypt
except ImportError:
    crypt = None


def generate_classic_tripcode(password: str) -> str:
    """
    Generates standard 10-character 2channel DES tripcode.
    """
    try:
        pw_bytes = password.encode('shift_jis')
    except (UnicodeEncodeError, LookupError):
        pw_bytes = password.encode('utf-8', errors='ignore')

    # Salt extraction
    salt = (pw_bytes + b"H..")[1:3]
    salt = re.sub(rb'[^\.-z]', rb'.', salt)
    salt = salt.translate(bytes.maketrans(b':;<=>?@[\\]^_`', b'ABCDEFGabcdef'))

    if crypt is not None:
        try:
            hashed = crypt.crypt(pw_bytes.decode('latin-1'), salt.decode('latin-1'))
            return "!" + hashed[-10:]
        except Exception:
            pass

    # Modern Python fallback if crypt is unavailable
    digest = hashlib.sha256(pw_bytes + salt).digest()
    b64 = base64.b64encode(digest).decode('ascii').replace('+', '.').replace('/', '_')
    return "!" + b64[:10]


def generate_secure_tripcode(secret: str) -> str:
    """
    Generates a 12-character cryptographic secure tripcode salted
    with the server's unique SECRET_KEY.
    """
    if not secret:
        return ""

    server_salt = getattr(settings, 'SECRET_KEY', 'default-pyuchan-salt').encode('utf-8')
    secret_bytes = secret.encode('utf-8', errors='ignore')

    mac = hmac.new(server_salt, secret_bytes, hashlib.sha256).digest()
    b64 = base64.b64encode(mac).decode('ascii').replace('+', '.').replace('/', '_')
    return "!!" + b64[:12]


def parse_name_field(raw_name: str) -> tuple[str, str]:
    """
    Parses a name input containing optional regular (#) and secure (##) tripcodes.
    Returns (cleaned_name, formatted_tripcode).
    """
    if not raw_name:
        return "", ""

    name = raw_name.strip()

    if "##" in name:
        parts = name.split("##", 1)
        name_part = parts[0]
        secure_secret = parts[1]

        if "#" in name_part:
            clean_name, regular_secret = name_part.split("#", 1)
            reg_trip = generate_classic_tripcode(regular_secret)
            sec_trip = generate_secure_tripcode(secure_secret)
            return clean_name.strip(), f"{reg_trip}{sec_trip}"
        else:
            sec_trip = generate_secure_tripcode(secure_secret)
            return name_part.strip(), sec_trip

    elif "#" in name:
        clean_name, regular_secret = name.split("#", 1)
        return clean_name.strip(), generate_classic_tripcode(regular_secret)

    return name, ""
