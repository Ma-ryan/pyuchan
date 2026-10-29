from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Prefetch, Count, Q, F
from django.http import HttpResponseBadRequest, JsonResponse, Http404, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .captcha import (
    generate_captcha_challenge,
    get_captcha_text,
    render_captcha_image,
    is_captcha_required,
)
from .conf import (
    DEFAULT_BAN_DURATION_DAYS,
    THREADS_PER_PAGE,
    ARCHIVE_RETENTION_DAYS,
    REPORTS_PER_WINDOW,
    REPORT_WINDOW_SECONDS,
)
from .dnsbl import ProxyBlockedException
from .forms import PostForm
from .models import Board, Thread, Post, Ban, BannedFile
from .parser import parse_post_content, build_quote_resolver_map
from .services import (
    create_post,
    create_post_report,
    delete_post_media,
    delete_post_or_file,
    BannedException,
    BannedFileException,
    DuplicateFileException,
    CooldownException,
    DuplicatePostException,
    WordBlockedException,
    ThreadFullException,
    log_staff_action,
    move_thread_to_board,
)


def get_client_ip(request):
    if getattr(settings, 'USE_X_FORWARDED_FOR', False):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def is_staff_check(user):
    return user.is_authenticated and user.is_staff


def get_safe_redirect(request, fallback_url: str):
    referer = request.META.get('HTTP_REFERER')
    if referer and url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure()
    ):
        return redirect(referer)
    return redirect(fallback_url)


def root_index(request):
    first_board = Board.objects.order_by('uri').first()
    if first_board:
        return redirect('board:index', uri=first_board.uri)
    return render(request, 'board/base.html', {'board': None})


@require_POST
@user_passes_test(is_staff_check)
def mod_move_thread(request, uri, thread_id):
    # Scope the thread to the board named in the URL.
    get_object_or_404(Thread, id=thread_id, board__uri=uri)

    target_uri = request.POST.get('target_board_uri', '').strip()
    ok, msg, moved_id = move_thread_to_board(thread_id, target_uri, request.user)

    if ok:
        messages.success(request, f"Thread moved to /{target_uri}/.")
        return redirect('board:thread', uri=target_uri, thread_id=moved_id)
    else:
        messages.error(request, msg)
        return redirect('board:thread', uri=uri, thread_id=thread_id)


@require_POST
@user_passes_test(is_staff_check)
def mod_notice_post(request, uri):
    board = get_object_or_404(Board, uri=uri)
    post_id = request.POST.get('post_id', '').strip()
    notice_text = request.POST.get('notice_text', '').strip()[:255]

    if not post_id.isdigit():
        raise Http404("Invalid post ID.")
    post = get_object_or_404(Post, id=post_id, board=board)
    post.mod_notice = notice_text
    post.save(update_fields=['mod_notice'])

    action_label = f"Attached notice: '{notice_text}'" if notice_text else "Cleared staff notice"
    log_staff_action(
        staff_user=request.user,
        action='attach_notice',
        board=board,
        details=f"{action_label} on post No. {post.post_num}",
        post_num=post.post_num,
        ip_hash=post.ip_hash
    )

    messages.success(request, f"Updated staff notice on No. {post.post_num}.")
    return get_safe_redirect(request, reverse('board:index', kwargs={'uri': uri}))


def archive_view(request, uri):
    board = get_object_or_404(Board, uri=uri)

    archived_threads = list(
        Thread.objects.filter(board=board, is_archived=True)
        .annotate(
            image_replies_count=Count('posts', filter=Q(posts__is_op=False, posts__image__isnull=False) & ~Q(posts__image=""))
        )
        .order_by('-archived_at')
    )

    thread_ids = [t.id for t in archived_threads]

    op_map = {}
    if thread_ids:
        ops = Post.objects.filter(thread_id__in=thread_ids, is_op=True)
        op_map = {p.thread_id: p for p in ops}

    archive_rows = []
    for t in archived_threads:
        op = op_map.get(t.id)
        if not op:
            continue
        snippet = op.content[:120] if op.content else ""
        archive_rows.append({
            'thread': t,
            'op': op,
            'snippet': snippet,
            'post_count': t.post_count,
            'image_count': t.image_replies_count,
        })

    return render(request, 'board/archive.html', {
        'board': board,
        'archive_rows': archive_rows,
        'ARCHIVE_RETENTION_DAYS': ARCHIVE_RETENTION_DAYS,
    })


def captcha_image(request):
    if request.GET.get('json') == '1':
        _, token = generate_captcha_challenge()
        return JsonResponse({
            'token': token,
            'img_url': f"{reverse('board:captcha_image')}?token={token}"
        })

    token = request.GET.get('token', '').strip()
    text = get_captcha_text(token) if token else None

    if not text:
        text, _ = generate_captcha_challenge()

    img_io = render_captcha_image(text)
    return HttpResponse(img_io.getvalue(), content_type='image/png')


def thread_updates(request, uri, thread_id):
    thread = get_object_or_404(Thread, id=thread_id, board__uri=uri)
    try:
        after_post_num = int(request.GET.get('after', 0))
    except (ValueError, TypeError):
        after_post_num = 0

    posts = list(
        thread.posts
        .filter(post_num__gt=after_post_num)
        .prefetch_related('backlinks__board')
        .order_by('post_num')
    )

    op_post = thread.posts.filter(is_op=True).first()
    op_num = op_post.post_num if op_post else None
    is_staff = is_staff_check(request.user)

    local_map, cross_map, valid_boards = build_quote_resolver_map(posts, uri)

    html_payload = ""
    new_last_num = after_post_num

    for p in posts:
        item = {
            'post': p,
            'content': parse_post_content(
                p.content,
                board_uri=uri,
                op_post_num=op_num,
                local_map=local_map,
                cross_map=cross_map,
                valid_boards=valid_boards,
            ),
            'backlinks': list(p.backlinks.all()),
        }
        html_payload += render_to_string('board/reply_partial.html', {
            'item': item,
            'is_staff': is_staff,
            'board': thread.board,
        }, request=request)
        new_last_num = p.post_num

    return JsonResponse({
        'count': len(posts),
        'html': html_payload,
        'last_num': new_last_num,
        'is_locked': thread.is_locked,
        'is_sticky': thread.is_sticky,
    })


@require_POST
def report_post(request, uri):
    board = get_object_or_404(Board, uri=uri)
    post_ids = request.POST.getlist('delete_post')[:10]
    reason = request.POST.get('report_reason', '').strip() or 'Rule violation'

    if not post_ids:
        messages.error(request, "No posts selected to report.")
        return get_safe_redirect(request, reverse('board:index', kwargs={'uri': uri}))

    client_ip = get_client_ip(request)
    success_count = 0

    # Per-IP rate limit: at most REPORTS_PER_WINDOW new reports per window,
    # in addition to the per-post 1-hour dedupe inside create_post_report().
    report_key = f"report_limit:{client_ip}"
    reports_used = cache.get(report_key, 0)
    if reports_used >= REPORTS_PER_WINDOW:
        messages.error(request, "Too many reports from your connection. Please try again later.")
        return get_safe_redirect(request, reverse('board:index', kwargs={'uri': uri}))

    for pid in post_ids:
        try:
            ok, _ = create_post_report(int(pid), reason, client_ip)
            if ok:
                success_count += 1
        except (ValueError, TypeError):
            continue

    if success_count > 0:
        cache.set(report_key, reports_used + success_count, timeout=REPORT_WINDOW_SECONDS)
        messages.success(request, f"Reported {success_count} post(s).")
    else:
        messages.error(request, "Unable to report selected post(s).")

    return get_safe_redirect(request, reverse('board:index', kwargs={'uri': uri}))


@require_POST
def delete_post_view(request, uri):
    board = get_object_or_404(Board, uri=uri)

    # 1. Throttle repeated failed deletion passwords per IP
    client_ip = get_client_ip(request)
    fail_cache_key = f"del_fail:{client_ip}"
    failed_attempts = cache.get(fail_cache_key, 0)
    if failed_attempts >= 5:
        return HttpResponseBadRequest("Too many failed deletion attempts. Please wait 5 minutes.")

    # 2. Cap post IDs at 10
    post_ids = request.POST.getlist('delete_post')[:10]
    password = request.POST.get('password', '').strip()
    file_only = request.POST.get('file_only') == 'on'

    if not post_ids:
        return HttpResponseBadRequest("No posts selected for deletion.")

    if not password:
        return HttpResponseBadRequest("Deletion password required.")

    thread_deleted = False
    any_success = False
    request_failures = 0

    for pid in post_ids:
        # Break immediately once 5 failures are accumulated
        if failed_attempts >= 5:
            break

        try:
            success, msg, is_thread_removal = delete_post_or_file(int(pid), password, file_only=file_only)
            if success:
                any_success = True
                if is_thread_removal:
                    thread_deleted = True
            elif msg == "Invalid password.":
                # Only genuine wrong-password failures count toward the lockout.
                # A batch that contains at least one wrong password never resets
                # the counter, even if one of the attacker's own posts succeeds
                # in the same request (piggybacked horizontal spray).
                request_failures += 1
                failed_attempts += 1
                cache.set(fail_cache_key, failed_attempts, timeout=300)
        except ValueError:
            continue

    # Clean slate only when this request had a success and zero
    # wrong-password failures of its own (pre-existing counter excluded).
    if any_success and request_failures == 0:
        cache.delete(fail_cache_key)

    if thread_deleted:
        return redirect('board:index', uri=board.uri)

    return get_safe_redirect(request, reverse('board:index', kwargs={'uri': uri}))


@require_POST
@user_passes_test(is_staff_check)
def mod_delete_post(request, uri):
    board = get_object_or_404(Board, uri=uri)
    post_ids = request.POST.getlist('delete_post')
    file_only = request.POST.get('file_only') == 'on'

    thread_deleted = False
    for pid in post_ids:
        try:
            post = Post.objects.select_related('thread').get(id=int(pid), board=board)
            thread = post.thread

            if file_only:
                delete_post_media(post)
                post.save()
                log_staff_action(
                    staff_user=request.user,
                    action='delete_file',
                    board=board,
                    details=f"Removed media from post No. {post.post_num}",
                    post_num=post.post_num,
                    ip_hash=post.ip_hash
                )
            else:
                if post.is_op:
                    p_num = post.post_num
                    p_ip = post.ip_hash
                    for reply in thread.posts.all():
                        delete_post_media(reply)
                    thread.delete()
                    thread_deleted = True
                    log_staff_action(
                        staff_user=request.user,
                        action='delete_thread',
                        board=board,
                        details=f"Deleted entire thread (OP No. {p_num})",
                        post_num=p_num,
                        ip_hash=p_ip
                    )
                else:
                    delete_post_media(post)
                    p_num = post.post_num
                    p_ip = post.ip_hash
                    post.delete()
                    thread.post_count = F('post_count') - 1
                    thread.save(update_fields=['post_count'])
                    log_staff_action(
                        staff_user=request.user,
                        action='delete_post',
                        board=board,
                        details=f"Deleted reply No. {p_num} in thread {thread.id}",
                        post_num=p_num,
                        ip_hash=p_ip
                    )
        except (Post.DoesNotExist, ValueError):
            continue

    if thread_deleted:
        return redirect('board:index', uri=board.uri)
    return get_safe_redirect(request, reverse('board:index', kwargs={'uri': uri}))


@require_POST
@user_passes_test(is_staff_check)
def mod_toggle_thread(request, uri, thread_id, action):
    thread = get_object_or_404(Thread, id=thread_id, board__uri=uri)

    if action == 'sticky':
        thread.is_sticky = not thread.is_sticky
        thread.save(update_fields=['is_sticky'])
        state_str = "Stickied" if thread.is_sticky else "Unstickied"
        log_staff_action(
            staff_user=request.user,
            action='toggle_sticky',
            board=thread.board,
            details=f"{state_str} thread {thread.id}"
        )
    elif action == 'lock':
        thread.is_locked = not thread.is_locked
        thread.save(update_fields=['is_locked'])
        state_str = "Locked" if thread.is_locked else "Unlocked"
        log_staff_action(
            staff_user=request.user,
            action='toggle_lock',
            board=thread.board,
            details=f"{state_str} thread {thread.id}"
        )

    return get_safe_redirect(request, reverse('board:thread', kwargs={'uri': uri, 'thread_id': thread.id}))


@require_POST
@user_passes_test(is_staff_check)
def mod_ban_user(request, uri):
    board = get_object_or_404(Board, uri=uri)
    post_id = request.POST.get('post_id', '').strip()
    reason = request.POST.get('reason', 'Rule violation').strip()
    is_global = request.POST.get('is_global') == 'on'
    is_permanent = request.POST.get('is_permanent') == 'on'

    if not post_id.isdigit():
        raise Http404("Invalid post ID.")
    post = get_object_or_404(Post, id=post_id, board=board)

    if is_permanent:
        expires = None
    else:
        try:
            duration_val = int(request.POST.get('duration_value', DEFAULT_BAN_DURATION_DAYS))
        except (ValueError, TypeError):
            duration_val = DEFAULT_BAN_DURATION_DAYS

        unit = request.POST.get('duration_unit', 'days')
        if duration_val <= 0:
            expires = None
        elif unit == 'hours':
            expires = timezone.now() + timedelta(hours=duration_val)
        elif unit == 'weeks':
            expires = timezone.now() + timedelta(weeks=duration_val)
        else:
            expires = timezone.now() + timedelta(days=duration_val)

    Ban.objects.create(
        board=None if is_global else board,
        ip_hash=post.ip_hash,
        reason=reason or 'Rule violation',
        expires_at=expires,
        staff_user=request.user
    )

    scope_str = "Global" if is_global else f"/{board.uri}/"
    dur_str = "Permanent" if is_permanent else f"{duration_val} {unit}"
    log_staff_action(
        staff_user=request.user,
        action='ban_user',
        board=board if not is_global else None,
        details=f"Issued {dur_str} ban [{scope_str}]. Reason: '{reason}'",
        post_num=post.post_num,
        ip_hash=post.ip_hash
    )

    if post.file_hash and request.POST.get('ban_file_hash') == 'on':
        BannedFile.objects.get_or_create(
            file_hash=post.file_hash,
            defaults={'reason': reason or 'Prohibited media', 'staff_user': request.user}
        )

    if request.POST.get('attach_ban_notice') == 'on':
        notice_preset = request.POST.get('ban_notice_text', '(USER WAS BANNED FOR THIS POST)').strip()
        post.mod_notice = notice_preset
        post.save(update_fields=['mod_notice'])

    if request.POST.get('delete_after_ban') == 'on':
        thread = post.thread
        if post.is_op:
            for reply in thread.posts.all():
                delete_post_media(reply)
            thread.delete()
        else:
            delete_post_media(post)
            post.delete()
            thread.post_count = F('post_count') - 1
            thread.save(update_fields=['post_count'])

    return get_safe_redirect(request, reverse('board:index', kwargs={'uri': uri}))


def catalog(request, uri):
    board = get_object_or_404(Board, uri=uri)

    if request.GET.get('reset') == '1':
        response = redirect('board:catalog', uri=uri)
        response.delete_cookie('catalog_sort')
        response.delete_cookie('catalog_size')
        return response

    valid_sorts = ('bump', 'date', 'replies')
    sort_from_get = request.GET.get('sort')
    if sort_from_get in valid_sorts:
        sort = sort_from_get
        update_sort_cookie = True
    else:
        sort = request.COOKIES.get('catalog_sort', 'bump')
        if sort not in valid_sorts:
            sort = 'bump'
        update_sort_cookie = False

    valid_sizes = ('small', 'large')
    size_from_get = request.GET.get('size')
    if size_from_get in valid_sizes:
        size = size_from_get
        update_size_cookie = True
    else:
        size = request.COOKIES.get('catalog_size', 'small')
        if size not in valid_sizes:
            size = 'small'
        update_size_cookie = False

    query = request.GET.get('q', '').strip()

    thread_qs = Thread.objects.filter(board=board, is_archived=False).annotate(
        image_replies_count=Count('posts', filter=Q(posts__is_op=False, posts__image__isnull=False) & ~Q(posts__image=""))
    )

    if sort == 'date':
        thread_qs = thread_qs.order_by('-is_sticky', '-created_at')
    elif sort == 'replies':
        thread_qs = thread_qs.order_by('-is_sticky', '-post_count')
    else:
        thread_qs = thread_qs.order_by('-is_sticky', '-last_bumped')

    threads = list(thread_qs)
    thread_ids = [t.id for t in threads]

    op_map = {}
    if thread_ids:
        ops = Post.objects.filter(thread_id__in=thread_ids, is_op=True)
        op_map = {p.thread_id: p for p in ops}

    catalog_items = []
    for t in threads:
        op = op_map.get(t.id)
        if not op:
            continue

        if query:
            q_lower = query.lower()
            subject_match = op.subject and q_lower in op.subject.lower()
            content_match = op.content and q_lower in op.content.lower()
            if not (subject_match or content_match):
                continue

        catalog_items.append({
            'thread': t,
            'op': op,
            'reply_count': max(0, t.post_count - 1),
            'image_count': t.image_replies_count,
        })

    response = render(request, 'board/catalog.html', {
        'board': board,
        'catalog_items': catalog_items,
        'sort': sort,
        'size': size,
        'query': query,
    })

    max_age = 365 * 24 * 60 * 60
    if update_sort_cookie:
        response.set_cookie('catalog_sort', sort, max_age=max_age, samesite='Lax')
    if update_size_cookie:
        response.set_cookie('catalog_size', size, max_age=max_age, samesite='Lax')

    return response


def board_index(request, uri, page=1):
    board = get_object_or_404(Board, uri=uri)
    is_staff = is_staff_check(request.user)

    if request.method == 'POST':
        form = PostForm(request.POST, request.FILES, is_thread=True, user=request.user)
        if form.is_valid():
            try:
                password = form.cleaned_data.get('password')
                post = create_post(
                    board_id=board.id,
                    name=form.cleaned_data.get('name'),
                    email=form.cleaned_data.get('email'),
                    subject=form.cleaned_data.get('subject'),
                    content=form.cleaned_data.get('content'),
                    password=password,
                    file=request.FILES.get('file'),
                    ip=get_client_ip(request),
                    is_spoiler=form.cleaned_data.get('spoiler', False),
                    capcode=form.cleaned_data.get('capcode', 'none'),
                )
                response = redirect('board:thread', uri=uri, thread_id=post.thread.id)
                if password:
                    response.set_cookie(
                        'post_password',
                        password,
                        max_age=365 * 24 * 3600,
                        path='/',
                        samesite='Lax',
                        secure=not settings.DEBUG,  # httponly must stay False: JS reads it
                    )
                return response
            except BannedException as e:
                return render(request, 'board/banned.html', {
                    'board': board,
                    'ban': e.ban,
                }, status=403)
            except (
                CooldownException,
                DuplicatePostException,
                DuplicateFileException,
                BannedFileException,
                ProxyBlockedException,
                WordBlockedException,
                ThreadFullException,
                ValidationError,
            ) as e:
                msg = e.messages[0] if hasattr(e, 'messages') else str(e)
                form.add_error('file' if 'size' in str(e).lower() or 'type' in str(e).lower() else None, msg)
    else:
        form = PostForm(is_thread=True, user=request.user)

    try:
        page_num = int(page)
    except (ValueError, TypeError):
        page_num = 1

    if board.max_pages > 0 and page_num > board.max_pages:
        raise Http404("Page does not exist.")

    thread_queryset = Thread.objects.filter(board=board, is_archived=False).order_by('-is_sticky', '-last_bumped')
    paginator = Paginator(thread_queryset, THREADS_PER_PAGE)

    try:
        page_obj = paginator.page(page_num)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages if paginator.num_pages > 0 else 1)

    if board.max_pages > 0:
        total_allowed = min(paginator.num_pages, board.max_pages)
        display_page_range = range(1, total_allowed + 1)
        has_next_page = page_obj.has_next() and (page_obj.number < total_allowed)
    else:
        display_page_range = paginator.page_range
        has_next_page = page_obj.has_next()

    threads_on_page = list(page_obj)
    thread_ids = [t.id for t in threads_on_page]

    page_posts_to_parse = []
    threads_data = []

    if thread_ids:
        ops_dict = {
            p.thread_id: p
            for p in Post.objects.filter(thread_id__in=thread_ids, is_op=True)
        }

        for thread in threads_on_page:
            op = ops_dict.get(thread.id)
            if not op:
                continue

            replies = list(
                thread.posts.filter(is_op=False)
                .prefetch_related('backlinks__board')
                .order_by('-post_num')[:5]
            )
            replies.reverse()

            total_replies = max(0, thread.post_count - 1)
            omitted = max(0, total_replies - len(replies))

            threads_data.append({
                'thread': thread,
                'op': op,
                'reply_count': total_replies,
                'omitted_count': omitted,
                'preview_replies': replies,
            })
            page_posts_to_parse.append(op)
            page_posts_to_parse.extend(replies)

    local_map, cross_map, valid_boards = build_quote_resolver_map(page_posts_to_parse, uri)

    thread_previews = []
    for td in threads_data:
        op = td['op']
        op_num = op.post_num if op else None

        thread_previews.append({
            'thread': td['thread'],
            'op': op,
            'op_content': parse_post_content(
                op.content,
                board_uri=uri,
                op_post_num=op_num,
                local_map=local_map,
                cross_map=cross_map,
                valid_boards=valid_boards
            ),
            'reply_count': td['reply_count'],
            'omitted_count': td['omitted_count'],
            'replies': [
                {
                    'post': r,
                    'content': parse_post_content(
                        r.content,
                        board_uri=uri,
                        op_post_num=op_num,
                        local_map=local_map,
                        cross_map=cross_map,
                        valid_boards=valid_boards
                    ),
                    'backlinks': list(r.backlinks.all())
                }
                for r in td['preview_replies']
            ],
        })

    captcha_token = ""
    if is_captcha_required(is_thread=True):
        _, captcha_token = generate_captcha_challenge()

    return render(request, 'board/index.html', {
        'board': board,
        'thread_previews': thread_previews,
        'page_obj': page_obj,
        'page_range': display_page_range,
        'has_next_page': has_next_page,
        'form': form,
        'captcha_token': captcha_token,
    })


def thread_detail(request, uri, thread_id):
    thread = get_object_or_404(Thread, id=thread_id, board__uri=uri)
    board = thread.board
    is_staff = is_staff_check(request.user)

    if request.method == 'POST':
        if thread.is_archived or (thread.is_locked and not is_staff):
            return HttpResponseBadRequest("This thread is archived/locked. Replies are disabled.")

        form = PostForm(request.POST, request.FILES, is_thread=False, user=request.user)
        if form.is_valid():
            try:
                password = form.cleaned_data.get('password')
                post = create_post(
                    board_id=board.id,
                    thread_id=thread.id,
                    name=form.cleaned_data.get('name'),
                    email=form.cleaned_data.get('email'),
                    subject=form.cleaned_data.get('subject'),
                    content=form.cleaned_data.get('content'),
                    password=password,
                    file=request.FILES.get('file'),
                    ip=get_client_ip(request),
                    is_spoiler=form.cleaned_data.get('spoiler', False),
                    capcode=form.cleaned_data.get('capcode', 'none'),
                )

                response = redirect(f"{request.path}#p{post.post_num}")
                if password:
                    response.set_cookie(
                        'post_password',
                        password,
                        max_age=365 * 24 * 3600,
                        path='/',
                        samesite='Lax',
                        secure=not settings.DEBUG,  # httponly must stay False: JS reads it
                    )
                return response
            except BannedException as e:
                return render(request, 'board/banned.html', {
                    'board': board,
                    'ban': e.ban,
                }, status=403)
            except (
                CooldownException,
                DuplicatePostException,
                DuplicateFileException,
                BannedFileException,
                ProxyBlockedException,
                WordBlockedException,
                ThreadFullException,
                ValidationError,
            ) as e:
                msg = e.messages[0] if hasattr(e, 'messages') else str(e)
                form.add_error('file' if 'size' in str(e).lower() or 'type' in str(e).lower() else None, msg)
    else:
        quote_num = request.GET.get('quote')
        initial_data = {}
        if quote_num and quote_num.isdigit():
            initial_data['content'] = f">>{quote_num}\n"

        form = PostForm(initial=initial_data, is_thread=False, user=request.user)

    posts = list(thread.posts.prefetch_related('backlinks__board').order_by('post_num'))
    op_post = posts[0] if posts else None
    op_num = op_post.post_num if op_post else None

    local_map, cross_map, valid_boards = build_quote_resolver_map(posts, uri)

    rendered_posts = [
        {
            'post': p,
            'content': parse_post_content(
                p.content,
                board_uri=uri,
                op_post_num=op_num,
                local_map=local_map,
                cross_map=cross_map,
                valid_boards=valid_boards
            ),
            'backlinks': list(p.backlinks.all()),
        }
        for p in posts
    ]

    captcha_token = ""
    if is_captcha_required(is_thread=False):
        _, captcha_token = generate_captcha_challenge()

    return render(request, 'board/thread.html', {
        'board': board,
        'thread': thread,
        'posts': rendered_posts,
        'form': form,
        'captcha_token': captcha_token,
    })
