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
                hint="Set DJANGO_SECRET_KEY in your environment or define SECRET_KEY in board/config.py.",
                id="board.E001",
            )
        ]
    return []
