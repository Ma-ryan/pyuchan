import zoneinfo
from urllib.parse import unquote
from django.utils import timezone

class UserTimezoneMiddleware:
    """
    Activates the visitor's local timezone if supplied via the user_tz cookie.
    Falls back cleanly to settings.TIME_ZONE if missing or invalid.

    Only canonical IANA zone names are accepted: ZoneInfo() also treats its
    argument as a filesystem path, so arbitrary cookie values must be rejected
    before construction.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tzname = request.COOKIES.get('user_tz')
        if tzname:
            tzname = unquote(tzname)
            if tzname in zoneinfo.available_timezones():
                try:
                    timezone.activate(zoneinfo.ZoneInfo(tzname))
                except Exception:
                    timezone.deactivate()
            else:
                timezone.deactivate()
        else:
            timezone.deactivate()

        return self.get_response(request)
