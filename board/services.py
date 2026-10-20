import hashlib
import os
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, F
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password

from .conf import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    THREADS_PER_PAGE,
    DEFAULT_ANONYMOUS_NAME,
    THREAD_COOLDOWN_SECONDS,
    REPLY_COOLDOWN_SECONDS,
    DUPLICATE_CHECK_WINDOW_SECONDS,
    ENABLE_ARCHIVE,
    ARCHIVE_RETENTION_DAYS,
)
from .dnsbl import check_ip_reputation, ProxyBlockedException
from .models import Ban, Board, Post, Thread, Report, BannedFile, WordFilter, AuditLog
from .tasks import process_post_media_task
from .tripcode import parse_name_field


# --- Staff Audit Logging ---

def log_staff_action(staff_user, action: str, board=None, details: str = "", post_num: int | None = None, ip_hash: str = ""):
    if not staff_user or not staff_user.is_authenticated:
        return

    AuditLog.objects.create(
        staff_user=staff_user,
        board=board,
        action=action,
        details=details.strip(),
        target_post_num=post_num,
        target_ip_hash=ip_hash or "",
    )


# --- Thread Transfer / Relocation ---

def move_thread_to_board(thread_id: int, destination_board_uri: str, staff_user) -> tuple[bool, str, int | None]:
    dest_board = Board.objects.filter(uri=destination_board_uri).first()
    if not dest_board:
        return False, f"Destination board /{destination_board_uri}/ does not exist.", None

    try:
        with transaction.atomic():
            thread = Thread.objects.select_for_update().select_related('board').get(id=thread_id)
            src_board = thread.board

            if src_board.id == dest_board.id:
                return False, "Source and destination boards are identical.", None

            dest_board = Board.objects.select_for_update().get(id=dest_board.id)

            posts = list(thread.posts.select_for_update().order_by('post_num'))
            if not posts:
                return False, "Cannot move an empty thread.", None

            old_op_num = posts[0].post_num

            num_map = {}
            for p in posts:
                dest_board.last_post_number += 1
                num_map[p.post_num] = dest_board.last_post_number

            dest_board.save(update_fields=['last_post_number'])

            thread.board = dest_board
            thread.save(update_fields=['board'])
            Report.objects.filter(post__thread=thread).update(board=dest_board)

            for p in posts:
                old_num = p.post_num
                new_num = num_map[old_num]

                def remap_quotes(match):
                    quoted_num = int(match.group(1))
                    if quoted_num in num_map:
                        return f">>{num_map[quoted_num]}"
                    else:
                        return f">>>/{src_board.uri}/{quoted_num}"

                if p.content:
                    p.content = re.sub(r'(?<![>/])>>(\d+)', remap_quotes, p.content)

                p.board = dest_board
                p.post_num = new_num
                p.save(update_fields=['board', 'post_num', 'content'])

            thread.post_count = len(posts)
            thread.save(update_fields=['post_count'])

            new_op_num = num_map[old_op_num]

            log_staff_action(
                staff_user=staff_user,
                action='move_thread',
                board=dest_board,
                details=f"Moved thread {thread.id} from /{src_board.uri}/ (OP No. {old_op_num}) to /{dest_board.uri}/ (New OP No. {new_op_num})",
                post_num=new_op_num
            )

            return True, "Thread moved successfully.", thread.id

    except Thread.DoesNotExist:
        return False, "Thread not found.", None
    except Exception as e:
        return False, f"Failed to move thread: {e}", None


# --- Anti-Spam, Blacklists & Integrity ---

def check_banned_file_hash(file_hash: str):
    if not file_hash:
        return
    banned = BannedFile.objects.filter(file_hash=file_hash).first()
    if banned:
        raise BannedFileException(f"Upload rejected: This file is blacklisted ({banned.reason}).")


def check_duplicate_file_hash(ip_address: str, file_hash: str):
    if not file_hash:
        return
    ip_h = hash_ip(ip_address)
    window_start = timezone.now() - timedelta(seconds=DUPLICATE_CHECK_WINDOW_SECONDS)

    is_duplicate = Post.objects.filter(
        ip_hash=ip_h,
        file_hash=file_hash,
        created_at__gte=window_start
    ).exists()

    if is_duplicate:
        raise DuplicateFileException("Duplicate file detected. You recently uploaded this file.")


class BannedFileException(Exception):
    def __init__(self, reason="This file has been banned from being uploaded."):
        super().__init__(reason)


class DuplicateFileException(Exception):
    pass


class WordBlockedException(Exception):
    pass


class BannedException(Exception):
    def __init__(self, ban):
        self.ban = ban
        super().__init__(f"Banned: {ban.reason}")


class CooldownException(Exception):
    def __init__(self, remaining_seconds, is_thread=False):
        self.remaining_seconds = remaining_seconds
        self.is_thread = is_thread
        action = "thread" if is_thread else "post"
        super().__init__(f"Please wait {remaining_seconds} second(s) before making another {action}.")


class DuplicatePostException(Exception):
    pass


# --- Hashing & Anti-Spam Gateways ---

def hash_ip(ip: str, salt: str = "") -> str:
    if not ip:
        return ""
    combined = f"{ip}:{salt or settings.SECRET_KEY}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def check_ip_banned(ip_hash: str, board_id: int):
    active_bans = Ban.objects.filter(
        ip_hash=ip_hash
    ).filter(
        Q(board_id__isnull=True) | Q(board_id=board_id)
    )

    now = timezone.now()
    for ban in active_bans:
        if ban.expires_at is None or ban.expires_at > now:
            raise BannedException(ban)


def check_spam_cooldowns(ip_address: str, is_thread: bool = False):
    ip_h = hash_ip(ip_address)
    now = timezone.now()
    cooldown = THREAD_COOLDOWN_SECONDS if is_thread else REPLY_COOLDOWN_SECONDS

    last_post = Post.objects.filter(ip_hash=ip_h).order_by('-created_at').first()
    if last_post:
        elapsed = (now - last_post.created_at).total_seconds()
        if elapsed < cooldown:
            remaining = int(cooldown - elapsed) + 1
            raise CooldownException(remaining_seconds=remaining, is_thread=is_thread)


def check_duplicate_content(ip_address: str, content: str):
    if not content or len(content.strip()) < 4:
        return

    ip_h = hash_ip(ip_address)
    window_start = timezone.now() - timedelta(seconds=DUPLICATE_CHECK_WINDOW_SECONDS)

    is_duplicate = Post.objects.filter(
        ip_hash=ip_h,
        content=content.strip(),
        created_at__gte=window_start
    ).exists()

    if is_duplicate:
        raise DuplicatePostException("Duplicate comment detected. Please wait before reposting.")


def apply_word_filters(name: str, subject: str, content: str, ip: str = "", board_id: int | None = None) -> tuple[str, str, str]:
    active_filters = list(WordFilter.objects.filter(is_active=True))
    if not active_filters:
        return name, subject, content

    full_check_text = f"{name or ''}\n{subject or ''}\n{content or ''}"
    lower_check_text = full_check_text.lower()

    for wf in active_filters:
        if wf.action in ('reject', 'ban'):
            matched = False
            if wf.match_type == 'regex':
                try:
                    if re.search(wf.pattern, full_check_text, flags=re.IGNORECASE | re.MULTILINE):
                        matched = True
                except re.error:
                    continue
            else:
                if wf.pattern.lower() in lower_check_text:
                    matched = True

            if matched:
                if wf.action == 'ban' and ip:
                    ip_h = hash_ip(ip)
                    Ban.objects.get_or_create(
                        ip_hash=ip_h,
                        board=None,
                        defaults={
                            'reason': f"Automated ban: {wf.reason}",
                            'expires_at': timezone.now() + timedelta(days=3),
                        }
                    )
                    raise WordBlockedException(f"Post rejected: You have been banned ({wf.reason}).")

                raise WordBlockedException(f"Post rejected: Content contains prohibited text ({wf.reason}).")

    def do_replace(text: str, pattern: str, replacement: str, match_type: str) -> str:
        if not text or not pattern:
            return text
        if match_type == 'regex':
            try:
                return re.sub(pattern, replacement, text, flags=re.IGNORECASE | re.MULTILINE)
            except re.error:
                return text
        else:
            escaped = re.escape(pattern)
            return re.sub(escaped, replacement, text, flags=re.IGNORECASE)

    for wf in active_filters:
        if wf.action == 'replace':
            name = do_replace(name, wf.pattern, wf.replacement, wf.match_type)
            subject = do_replace(subject, wf.pattern, wf.replacement, wf.match_type)
            content = do_replace(content, wf.pattern, wf.replacement, wf.match_type)

    return name, subject, content


# --- Reports & Deletion ---

def create_post_report(post_id: int, reason: str, ip: str) -> tuple[bool, str]:
    ip_h = hash_ip(ip)

    try:
        post = Post.objects.select_related('board').get(id=post_id)
    except Post.DoesNotExist:
        return False, "Post does not exist."

    recent_report = Report.objects.filter(
        post=post,
        ip_hash=ip_h,
        is_cleared=False,
        created_at__gte=timezone.now() - timedelta(hours=1)
    ).exists()

    if recent_report:
        return False, "You have already reported this post recently."

    Report.objects.create(
        post=post,
        board=post.board,
        ip_hash=ip_h,
        reason=reason.strip() or "Rule violation"
    )
    return True, "Report submitted successfully."


def delete_post_media(post: Post) -> None:
    """
    Safely disassociates and deletes files on disk only if no other
    post references the same file path. Persists image=None in DB
    so batch loops do not see cleared references.
    """
    if post.image:
        image_path = post.image.name
        other_img_exists = Post.objects.filter(image=image_path).exclude(id=post.id).exists()
        if not other_img_exists:
            try:
                if os.path.isfile(post.image.path):
                    os.remove(post.image.path)
            except (OSError, ValueError):
                pass
        post.image = None

    if post.thumbnail:
        thumb_path = post.thumbnail.name
        other_thumb_exists = Post.objects.filter(thumbnail=thumb_path).exclude(id=post.id).exists()
        if not other_thumb_exists:
            try:
                if os.path.isfile(post.thumbnail.path):
                    os.remove(post.thumbnail.path)
            except (OSError, ValueError):
                pass
        post.thumbnail = None

    post.file_name = ""
    post.file_hash = ""
    post.file_size = 0
    post.image_width = None
    post.image_height = None
    post.is_file_deleted = True

    # Persist cleared references to the database immediately
    Post.objects.filter(id=post.id).update(
        image=None,
        thumbnail=None,
        file_name="",
        file_hash="",
        file_size=0,
        image_width=None,
        image_height=None,
        is_file_deleted=True
    )


def delete_post_or_file(post_id: int, password: str, file_only: bool = False) -> tuple[bool, str, bool]:
    with transaction.atomic():
        try:
            post = Post.objects.select_for_update().select_related('thread', 'board').get(id=post_id)
        except Post.DoesNotExist:
            return False, "Post does not exist.", False

        if not post.password:
            return False, "Post does not have a deletion password configured.", False

        if not check_password(password, post.password):
            return False, "Invalid password.", False

        thread = post.thread

        if file_only:
            if not post.image and not post.is_file_deleted:
                return False, "This post has no attached file.", False
            delete_post_media(post)
            return True, "File removed successfully.", False

        if post.is_op:
            for reply in thread.posts.all():
                delete_post_media(reply)
            thread.delete()
            return True, "Thread deleted successfully.", True
        else:
            delete_post_media(post)
            post.delete()
            thread.post_count = F('post_count') - 1
            thread.save(update_fields=['post_count'])
            return True, "Post deleted successfully.", False


# --- Media Ingestion ---

def prepare_raw_upload(uploaded_file):
    ext = os.path.splitext(uploaded_file.name)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"File type '{ext}' is not permitted.")

    if uploaded_file.size > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE // (1024 * 1024)
        raise ValidationError(f"File exceeds maximum allowed size of {max_mb} MB.")

    raw_data = uploaded_file.read()
    uploaded_file.seek(0)

    file_hash = hashlib.sha256(raw_data).hexdigest()
    orig_name = uploaded_file.name
    file_size = len(raw_data)

    clean_filename = f"{file_hash[:16]}{ext}"
    clean_file = ContentFile(raw_data, name=clean_filename)

    return clean_file, orig_name, file_hash, file_size


# --- Thread Pruning & Expiry ---

def prune_old_threads(board: Board) -> None:
    if board.max_pages <= 0:
        return

    max_threads = board.max_pages * THREADS_PER_PAGE

    active_thread_ids = list(
        Thread.objects.filter(board=board, is_archived=False)
        .order_by("-is_sticky", "-last_bumped")
        .values_list("id", flat=True)[:max_threads]
    )

    overflow_threads = (
        Thread.objects.filter(board=board, is_archived=False)
        .exclude(id__in=active_thread_ids)
    )

    if ENABLE_ARCHIVE:
        now = timezone.now()
        overflow_threads.update(is_archived=True, archived_at=now)
    else:
        overflow_posts = Post.objects.filter(thread__in=overflow_threads)
        for p in overflow_posts:
            delete_post_media(p)
        overflow_threads.delete()


def purge_expired_archived_threads():
    if not ENABLE_ARCHIVE:
        return

    cutoff = timezone.now() - timedelta(days=ARCHIVE_RETENTION_DAYS)
    expired_threads = Thread.objects.filter(is_archived=True, archived_at__lte=cutoff)

    expired_posts = Post.objects.filter(thread__in=expired_threads)
    for p in expired_posts:
        delete_post_media(p)

    expired_threads.delete()


# --- Master Post Creation ---

def create_post(
    board_id: int,
    thread_id: int | None = None,
    name: str | None = None,
    email: str | None = None,
    subject: str | None = None,
    content: str = "",
    password: str | None = None,
    file=None,
    ip: str | None = None,
    is_spoiler: bool = False,
    capcode: str = 'none',
) -> Post:
    if ip:
        check_ip_reputation(ip)

    ip_h = hash_ip(ip) if ip else ""
    if ip_h:
        check_ip_banned(ip_h, board_id)
        is_thread = (thread_id is None)
        check_spam_cooldowns(ip, is_thread=is_thread)
        check_duplicate_content(ip, content)

    name, subject, content = apply_word_filters(
        name=name,
        subject=subject,
        content=content,
        ip=ip,
        board_id=board_id
    )

    clean_email = (email or "").strip().lower()
    is_sage = clean_email == "sage"

    raw_name = (name or "").strip()
    clean_name, tripcode = parse_name_field(raw_name)

    if not clean_name and not tripcode:
        clean_name = DEFAULT_ANONYMOUS_NAME

    clean_image = None
    original_filename = ""
    file_hash = ""
    file_size = 0

    if file:
        clean_image, original_filename, file_hash, file_size = prepare_raw_upload(file)
        check_banned_file_hash(file_hash)
        if ip:
            check_duplicate_file_hash(ip, file_hash)

    with transaction.atomic():
        board = Board.objects.select_for_update().get(id=board_id)
        board.last_post_number += 1
        board.save(update_fields=["last_post_number"])

        is_op_post = (thread_id is None)

        if is_op_post:
            thread = Thread.objects.create(
                board=board,
                post_count=1,
                last_bumped=timezone.now(),
            )
            transaction.on_commit(lambda: prune_old_threads(board))
        else:
            thread = Thread.objects.select_for_update().get(id=thread_id, board=board)
            thread.post_count += 1

            can_bump = (board.bump_limit == 0) or (thread.post_count <= board.bump_limit)
            if not is_sage and can_bump and not thread.is_locked:
                thread.last_bumped = timezone.now()

            thread.save(update_fields=["post_count", "last_bumped"])

        post = Post.objects.create(
            board=board,
            thread=thread,
            post_num=board.last_post_number,
            is_op=is_op_post,
            name=clean_name,
            tripcode=tripcode,
            email=email.strip() if email else "",
            subject=subject.strip() if subject else "",
            content=content,
            password=make_password(password.strip()) if password and password.strip() else "",
            ip_hash=ip_h,
            image=clean_image,
            thumbnail=None,
            file_name=original_filename,
            file_hash=file_hash,
            file_size=file_size,
            image_width=None,
            image_height=None,
            is_spoiler=bool(is_spoiler and file),
            capcode=capcode if capcode in ('mod', 'admin') else 'none',
        )

        if content:
            local_nums = set(int(n) for n in re.findall(r"(?<![>/])>>(\d+)", content))
            if local_nums:
                target_posts = Post.objects.filter(
                    board=board,
                    post_num__in=local_nums,
                )
                post.mentions.add(*target_posts)

            cross_mentions = re.findall(r">>>/([a-zA-Z0-9_-]+)/(?:>>)?(\d+)", content)
            for target_uri, target_pnum in cross_mentions:
                target_post = Post.objects.filter(
                    board__uri=target_uri,
                    post_num=int(target_pnum),
                ).first()
                if target_post:
                    post.mentions.add(target_post)

        if file:
            transaction.on_commit(lambda: process_post_media_task(post.id))

        return post
