import zoneinfo
from urllib.parse import unquote
from django.utils import timezone

class UserTimezoneMiddleware:
    """
    Activates the visitor's local timezone if supplied via the user_tz cookie.
    Falls back cleanly to settings.TIME_ZONE if missing or invalid.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tzname = request.COOKIES.get('user_tz')
        if tzname:
            try:
                timezone.activate(zoneinfo.ZoneInfo(unquote(tzname)))
            except Exception:
                timezone.deactivate()
        else:
            timezone.deactivate()

        return self.get_response(request)
