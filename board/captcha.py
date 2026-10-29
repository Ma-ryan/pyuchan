import io
import os
import random
import secrets
import uuid
from PIL import Image, ImageDraw, ImageFont
from django.core.cache import cache
from .conf import (
    CAPTCHA_LENGTH,
    CAPTCHA_EXPIRY_SECONDS,
    ENABLE_CAPTCHA,
    CAPTCHA_ON_THREAD_CREATION,
    CAPTCHA_ON_REPLY,
)

CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def is_captcha_required(is_thread: bool = False) -> bool:
    if not ENABLE_CAPTCHA:
        return False
    return CAPTCHA_ON_THREAD_CREATION if is_thread else CAPTCHA_ON_REPLY


def generate_captcha_challenge() -> tuple[str, str]:
    """
    Generates a challenge string, stores it in cache under a random UUID token,
    and returns (text, token).
    """
    # Cryptographically secure challenge text. random.choices (Mersenne Twister)
    # is not suitable for security tokens: its state is recoverable from enough
    # outputs. The noise rendering below may keep using `random` (visual only).
    text = "".join(secrets.choice(CAPTCHA_CHARS) for _ in range(CAPTCHA_LENGTH))
    token = str(uuid.uuid4())
    cache_key = f"captcha:{token}"
    cache.set(cache_key, text.upper(), timeout=CAPTCHA_EXPIRY_SECONDS)
    return text, token


def get_captcha_text(token: str) -> str | None:
    """Retrieves cached challenge text without consuming it (used for image rendering)."""
    if not token:
        return None
    return cache.get(f"captcha:{token.strip()}")


def verify_captcha_response(token: str, user_answer: str) -> bool:
    """
    Atomically verifies and consumes a CAPTCHA challenge from cache.
    Prevents token reuse.
    """
    if not token or not user_answer:
        return False

    cache_key = f"captcha:{token.strip()}"
    expected_answer = cache.get(cache_key)

    if not expected_answer:
        return False

    # One-time consumption. Django 5.x cache.delete() returns True only if this
    # process actually removed the key, so a concurrent verification that
    # already consumed the token (multi-worker deployments) loses the race and
    # is rejected.
    if not cache.delete(cache_key):
        return False

    return expected_answer.strip().upper() == user_answer.strip().upper()


def render_captcha_image(text: str) -> io.BytesIO:
    width = 160
    height = 50
    bg_color = (238, 242, 255)
    fg_colors = [
        (15, 12, 93),
        (175, 10, 15),
        (17, 119, 67),
        (52, 52, 92),
    ]

    image = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(image)

    for _ in range(8):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line(((x1, y1), (x2, y2)), fill=random.choice(fg_colors), width=1)

    for _ in range(120):
        dot_x = random.randint(0, width)
        dot_y = random.randint(0, height)
        draw.point((dot_x, dot_y), fill=random.choice(fg_colors))

    font = None
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for path in font_paths:
        if os.path.isfile(path):
            try:
                font = ImageFont.truetype(path, 26)
                break
            except Exception:
                pass

    if font is None:
        font = ImageFont.load_default()

    char_spacing = (width - 24) // len(text)
    for i, char in enumerate(text):
        char_img = Image.new("RGBA", (40, 45), (255, 255, 255, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((8, 4), char, font=font, fill=random.choice(fg_colors))

        rotated = char_img.rotate(random.randint(-25, 25), resample=Image.Resampling.BILINEAR, expand=1)
        x_offset = 12 + (i * char_spacing) + random.randint(-2, 2)
        y_offset = random.randint(3, 8)
        image.paste(rotated, (x_offset, y_offset), rotated)

    out_io = io.BytesIO()
    image.save(out_io, format="PNG")
    out_io.seek(0)
    return out_io
