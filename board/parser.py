import html
import re
from .models import Post, Board


def build_quote_resolver_map(posts: list[Post], current_board_uri: str) -> tuple[dict[int, int], dict[tuple[str, int], int], set[str]]:
    """
    Scans a batch of posts for local (>>123) and cross-board (>>>/b/123) quotes,
    queries the database in bulk, and builds fast lookup maps.
    Returns:
      (local_map, cross_map, valid_boards)
    """
    local_needed = set()
    cross_needed = set()
    boards_referenced = set()

    # Pre-extract all quote references across the provided posts
    for p in posts:
        content = p.content or ""
        # Local quotes: (?<![>/])>>123
        # Numbers are capped at 9 digits so they can never overflow the
        # int4 post_num column on PostgreSQL/MySQL (or SQLite binding).
        for num_str in re.findall(r'(?<![>/])>>(\d{1,9})(?!\d)', content):
            local_needed.add(int(num_str))

        # Cross-board quotes: >>>/b/123 or >>>/b/>>123 or >>>/b/
        for b_uri, num_str in re.findall(r'(?<!>)>>>/([a-zA-Z0-9_-]+)/(?:(?:>>)?(\d{1,9})(?!\d))?', content):
            b_lower = b_uri.lower()
            boards_referenced.add(b_lower)
            if num_str:
                cross_needed.add((b_lower, int(num_str)))

    # 1. Batch query valid board URIs
    valid_boards = set(
        Board.objects.filter(uri__in=boards_referenced).values_list('uri', flat=True)
    )

    # 2. Batch query local post numbers -> thread_id
    local_map = {}
    if local_needed:
        local_qs = Post.objects.filter(
            board__uri=current_board_uri,
            post_num__in=local_needed
        ).values_list('post_num', 'thread_id')
        local_map = dict(local_qs)

    # 3. Batch query cross-board (board_uri, post_num) -> thread_id
    cross_map = {}
    if cross_needed:
        # Group by board to batch efficiently
        from collections import defaultdict
        grouped = defaultdict(list)
        for b_uri, p_num in cross_needed:
            if b_uri in valid_boards:
                grouped[b_uri].append(p_num)

        for b_uri, p_nums in grouped.items():
            qs = Post.objects.filter(
                board__uri=b_uri,
                post_num__in=p_nums
            ).values_list('post_num', 'thread_id')
            for p_num, t_id in qs:
                cross_map[(b_uri, p_num)] = t_id

    return local_map, cross_map, valid_boards


def parse_post_content(
    raw_content: str,
    board_uri: str,
    op_post_num: int | None = None,
    local_map: dict[int, int] | None = None,
    cross_map: dict[tuple[str, int], int] | None = None,
    valid_boards: set[str] | None = None,
) -> str:
    """
    Parses user post content into safe HTML.
    Uses pre-resolved lookup maps when provided to eliminate N+1 queries.
    """
    if not raw_content:
        return ""

    escaped_text = html.escape(raw_content)

    # 1. Spoilers: [spoiler]...[/spoiler] or [s]...[/s]
    spoiler_pattern = re.compile(r'\[(?:spoiler|s)\](.*?)\[/(?:spoiler|s)\]', re.IGNORECASE | re.DOTALL)
    escaped_text = spoiler_pattern.sub(r'<span class="spoiler">\1</span>', escaped_text)

    # 2. Cross-board quotes: >>>/b/123 or >>>/b/
    def replace_cross_board(match):
        target_board = match.group(1).lower()
        post_num_str = match.group(2)

        # Board validation
        if valid_boards is not None:
            is_valid_board = target_board in valid_boards
        else:
            is_valid_board = Board.objects.filter(uri=target_board).exists()

        if not is_valid_board:
            return match.group(0)

        # Cross-board post link
        if post_num_str:
            target_post_num = int(post_num_str)
            if cross_map is not None:
                thread_id = cross_map.get((target_board, target_post_num))
            else:
                target_post = Post.objects.filter(board__uri=target_board, post_num=target_post_num).values('thread_id').first()
                thread_id = target_post['thread_id'] if target_post else None

            if thread_id:
                return (
                    f'<a class="quotelink" href="/{target_board}/res/{thread_id}/#p{target_post_num}">'
                    f'&gt;&gt;&gt;/{target_board}/{target_post_num}</a>'
                )
            return f'<span class="quotelink-dead">&gt;&gt;&gt;/{target_board}/{target_post_num}</span>'

        # Target board index link: >>>/b/
        return f'<a class="quotelink" href="/{target_board}/">&gt;&gt;&gt;/{target_board}/</a>'

    cross_board_pattern = re.compile(r'(?<!&gt;)&gt;&gt;&gt;/([a-zA-Z0-9_-]+)/(?:(?:&gt;&gt;)?(\d{1,9})(?!\d))?')
    escaped_text = cross_board_pattern.sub(replace_cross_board, escaped_text)

    # 3. Local quotes: (?<![>/])>>123
    def replace_quote(match):
        post_num_str = match.group(1)
        post_num = int(post_num_str)

        if local_map is not None:
            thread_id = local_map.get(post_num)
        else:
            target_post = Post.objects.filter(board__uri=board_uri, post_num=post_num).values('thread_id').first()
            thread_id = target_post['thread_id'] if target_post else None

        if thread_id:
            is_op = (op_post_num is not None and post_num == op_post_num)
            suffix = " (OP)" if is_op else ""
            return f'<a class="quotelink" href="/{board_uri}/res/{thread_id}/#p{post_num}">&gt;&gt;{post_num}{suffix}</a>'

        return f'<span class="quotelink-dead">&gt;&gt;{post_num_str}</span>'

    # Lookbehind on escaped text: a raw '>' never appears here (it is '&gt;'),
    # so block a preceding '&gt;' (raw '>>>123' must stay dead) and a preceding
    # '/' (tail of an unmatched cross-board quote '>>>/b/>>123').
    escaped_text = re.sub(r'(?<!&gt;)(?<!/)&gt;&gt;(\d{1,9})(?!\d)(?:\s*\(OP\))?', replace_quote, escaped_text)

    # 4. Greentext handling
    lines = escaped_text.splitlines()
    processed_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('&gt;') and not stripped.startswith('&gt;&gt;'):
            processed_lines.append(f'<span class="quote">{line}</span>')
        else:
            processed_lines.append(line)

    return "<br>".join(processed_lines)
