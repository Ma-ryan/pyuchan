import os
import random
from datetime import timezone as py_timezone

from django import template
from django.conf import settings
from django.template.defaultfilters import date as django_date
from django.templatetags.static import static
from django.utils import timezone as dj_timezone
from django.utils.safestring import mark_safe

from board.conf import POST_DATE_FORMAT

register = template.Library()


@register.filter(name='post_date')
def post_date(dt):
    if not dt:
        return ""

    if dj_timezone.is_naive(dt):
        dt_aware = dj_timezone.make_aware(dt, py_timezone.utc)
    else:
        dt_aware = dt.astimezone(py_timezone.utc)

    # Server converts to the active timezone (from cookie or settings.TIME_ZONE)
    local_dt = dj_timezone.localtime(dt_aware)
    formatted = django_date(local_dt, POST_DATE_FORMAT)
    epoch_ms = int(dt_aware.timestamp() * 1000)

    # Embed data-utc so dynamic auto-updates in threads can still format new incoming replies
    return mark_safe(f'<span class="post-date" data-utc="{epoch_ms}">{formatted}</span>')


ALLOWED_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


@register.simple_tag
def random_banner():
    """
    Returns the static URL of a random image found in board/static/board/banners/.
    Returns an empty string if the directory is missing or empty.
    """
    banner_dir = os.path.join(settings.BASE_DIR, 'board', 'static', 'board', 'banners')

    if not os.path.isdir(banner_dir):
        return ''

    try:
        images = [
            f for f in os.listdir(banner_dir)
            if os.path.splitext(f)[1].lower() in ALLOWED_IMAGE_EXTS and not f.startswith('.')
        ]
        if not images:
            return ''

        chosen = random.choice(images)
        return static(f'board/banners/{chosen}')
    except OSError:
        return ''
