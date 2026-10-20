from .models import Board


def board_nav(request):
    """
    Evaluates all active boards into a single list once per request,
    preventing duplicate queries between top and bottom board navigation strips.
    """
    return {
        'all_boards': list(Board.objects.all().order_by('uri'))
    }
