import shutil
from django.conf import settings
from django.core.checks import Error, Warning, register
from .conf import FFMPEG_PATH, FFPROBE_PATH, ALLOWED_VIDEO_EXTENSIONS


@register()
def check_ffmpeg_installed(app_configs, **kwargs):
    errors = []

    # Only check if video uploads are enabled
    if ALLOWED_VIDEO_EXTENSIONS:
        if not shutil.which(FFMPEG_PATH):
            errors.append(
                Warning(
                    f"'{FFMPEG_PATH}' executable was not found on the system path.",
                    hint="Install ffmpeg via your package manager (e.g., 'sudo apt install ffmpeg') or update FFMPEG_PATH in board/config.py.",
                    id="board.W001",
                )
            )
        if not shutil.which(FFPROBE_PATH):
            errors.append(
                Warning(
                    f"'{FFPROBE_PATH}' executable was not found on the system path.",
                    hint="Install ffprobe or update FFPROBE_PATH in board/config.py.",
                    id="board.W002",
                )
            )

    return errors


@register()
def check_production_secret_key(app_configs, **kwargs):
    """
    Guarantees production environments never operate with transient or insecure keys.
    """
    if not settings.DEBUG and settings.SECRET_KEY.startswith('django-insecure-'):
        return [
            Error(
                "Production deployment is using a transient or default insecure SECRET_KEY.",
                hint="For local development, set DJANGO_DEBUG=True (e.g. DJANGO_DEBUG=True python manage.py runserver). For a real deployment, set DJANGO_SECRET_KEY in the environment or define SECRET_KEY in board/config.py.",
                id="board.E001",
            )
        ]
    return []


@register()
def check_shared_cache(app_configs, **kwargs):
    """
    LocMemCache is per-process. Under a multi-worker deployment (gunicorn etc.)
    CAPTCHA tokens and throttle counters are not shared between workers, which
    breaks posting. Flag it whenever production is not on a shared backend.
    """
    if settings.DEBUG:
        return []
    from django.core.cache import caches
    backend_module = caches['default'].__class__.__module__
    if 'locmem' in backend_module:
        return [
            Warning(
                "Production is using the per-process LocMemCache. CAPTCHA tokens and "
                "rate-limit counters are not shared between workers, which breaks "
                "posting under a multi-worker deployment.",
                hint="Set DJANGO_CACHE_REDIS_URL (e.g. redis://127.0.0.1:6379/1) or "
                     "define CACHES with a shared backend in config/settings.py.",
                id="board.W003",
            )
        ]
    return []


@register()
def check_transient_dev_secret_key(app_configs, **kwargs):
    """
    A transient development SECRET_KEY is regenerated on every process start.
    Because hash_ip() salts IP hashes with SECRET_KEY, bans, cooldowns and
    report correlations are invalidated across restarts.
    """
    if settings.DEBUG and settings.SECRET_KEY.startswith('django-insecure-'):
        return [
            Warning(
                "Running with a transient development SECRET_KEY (regenerated on every "
                "restart). IP hashes change on each start, so bans and cooldowns do not "
                "survive restarts.",
                hint="Set DJANGO_SECRET_KEY to a stable value even in local development.",
                id="board.W004",
            )
        ]
    return []
