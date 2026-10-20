import os
from django.db import models
from django.utils import timezone
from django.conf import settings
from django.templatetags.static import static

from .conf import ALLOWED_IMAGE_EXTENSIONS, ALLOWED_VIDEO_EXTENSIONS, FILE_ICON_MAP


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('delete_post', 'Delete Post'),
        ('delete_thread', 'Delete Thread'),
        ('delete_file', 'Delete File Only'),
        ('toggle_sticky', 'Toggle Sticky'),
        ('toggle_lock', 'Toggle Lock'),
        ('ban_user', 'Issue Ban'),
        ('remove_ban', 'Remove Ban'),
        ('move_thread', 'Move Thread'),
        ('word_filter_create', 'Create Word Filter'),
        ('clear_report', 'Clear Report'),
        ('attach_notice', 'Attach Public Notice'),
    ]

    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs'
    )
    board = models.ForeignKey(
        'Board',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    details = models.TextField(blank=True, default="")
    target_post_num = models.PositiveIntegerField(null=True, blank=True)
    target_ip_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['board', 'action', 'created_at']),
        ]

    def __str__(self):
        user_str = self.staff_user.username if self.staff_user else "System"
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {user_str} -> {self.get_action_display()}"


class WordFilter(models.Model):
    ACTION_CHOICES = [
        ('replace', 'Replace text (wordfilter)'),
        ('reject', 'Reject post'),
        ('ban', 'Reject and auto-ban IP'),
    ]

    MATCH_CHOICES = [
        ('exact', 'Substring match (case-insensitive)'),
        ('regex', 'Regular expression'),
    ]

    pattern = models.CharField(max_length=255, unique=True, help_text="Word or regular expression to match.")
    replacement = models.CharField(max_length=255, blank=True, default="", help_text="Replacement string (used when action is 'replace').")
    match_type = models.CharField(max_length=10, choices=MATCH_CHOICES, default='exact')
    action = models.CharField(max_length=10, choices=ACTION_CHOICES, default='replace')
    reason = models.CharField(max_length=255, default="Word replacement / filter")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        if self.action == 'replace':
            return f"[{self.match_type.upper()}] '{self.pattern}' -> '{self.replacement}'"
        return f"[{self.match_type.upper()}] {self.pattern} ({self.get_action_display()})"


class BannedFile(models.Model):
    file_hash = models.CharField(max_length=64, unique=True, db_index=True)
    reason = models.CharField(max_length=255, default='Prohibited content')
    created_at = models.DateTimeField(default=timezone.now)
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.file_hash[:12]}... - {self.reason}"


class Report(models.Model):
    post = models.ForeignKey('Post', on_delete=models.CASCADE, related_name='reports')
    board = models.ForeignKey('Board', on_delete=models.CASCADE, related_name='reports')
    ip_hash = models.CharField(max_length=64, db_index=True)
    reason = models.CharField(max_length=255, default="Rule violation")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_cleared = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['post', 'ip_hash', 'is_cleared', 'created_at']),
            models.Index(fields=['board', 'is_cleared', 'created_at']),
        ]

    def __str__(self):
        return f"Report on No. {self.post.post_num} (/{self.board.uri}/): {self.reason}"


class Board(models.Model):
    uri = models.SlugField(max_length=8, unique=True)
    title = models.CharField(max_length=64)
    subtitle = models.CharField(max_length=128, blank=True)
    last_post_number = models.PositiveIntegerField(default=0)
    bump_limit = models.PositiveSmallIntegerField(default=300)
    max_pages = models.PositiveSmallIntegerField(default=10)

    def __str__(self):
        return f"/{self.uri}/ - {self.title}"


class Thread(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name='threads')
    is_sticky = models.BooleanField(default=False, db_index=True)
    is_locked = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    post_count = models.PositiveIntegerField(default=0)
    last_bumped = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-is_sticky', '-last_bumped']
        indexes = [
            models.Index(fields=['board', 'is_archived', '-is_sticky', '-last_bumped']),
        ]

    def __str__(self):
        return f"Thread {self.id} on /{self.board.uri}/"


class Post(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name='posts')
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='posts')
    post_num = models.PositiveIntegerField(db_index=True)
    is_op = models.BooleanField(default=False, db_index=True)

    name = models.CharField(max_length=64, default="Anonymous")
    email = models.CharField(max_length=64, blank=True)
    tripcode = models.CharField(max_length=32, blank=True)
    subject = models.CharField(max_length=100, blank=True)
    content = models.TextField(blank=True, default="")

    ip_hash = models.CharField(max_length=64, db_index=True)

    image = models.FileField(upload_to='src/', blank=True, null=True)
    thumbnail = models.FileField(upload_to='thumb/', blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True)

    file_size = models.PositiveIntegerField(default=0)
    image_width = models.PositiveIntegerField(null=True, blank=True)
    image_height = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    is_file_deleted = models.BooleanField(default=False)
    password = models.CharField(max_length=128, blank=True)

    is_spoiler = models.BooleanField(default=False)

    CAPCODE_CHOICES = [
        ('none', 'None'),
        ('mod', 'Mod'),
        ('admin', 'Admin'),
    ]
    capcode = models.CharField(max_length=10, choices=CAPCODE_CHOICES, default='none', db_index=True)

    mod_notice = models.CharField(max_length=255, blank=True, default="")

    mentions = models.ManyToManyField(
        'self',
        symmetrical=False,
        related_name='backlinks',
        blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['board', 'post_num'],
                name='unique_post_num_per_board'
            )
        ]
        ordering = ['post_num']
        indexes = [
            models.Index(fields=['thread', 'post_num']),
            models.Index(fields=['ip_hash', 'created_at']),
            models.Index(fields=['board', 'created_at']),
        ]

    @property
    def file_ext(self) -> str:
        if not self.image:
            return ""
        return os.path.splitext(self.image.name)[1].lower()

    @property
    def is_image(self) -> bool:
        return self.file_ext in ALLOWED_IMAGE_EXTENSIONS

    @property
    def is_video(self) -> bool:
        return self.file_ext in ALLOWED_VIDEO_EXTENSIONS

    @property
    def is_non_image(self) -> bool:
        if not self.image:
            return False
        return not (self.is_image or self.is_video)

    @property
    def thumbnail_url(self) -> str:
        if self.is_spoiler:
            return static('board/img/spoiler.png')

        if self.is_non_image:
            icon_file = FILE_ICON_MAP.get(self.file_ext, 'generic.png')
            return static(f'board/icons/{icon_file}')

        if self.thumbnail:
            return self.thumbnail.url

        # If it's a video or large image still processing in Huey:
        if self.is_video:
            return static('board/img/processing.png')

        if self.image:
            return self.image.url

        return ""


class Ban(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, null=True, blank=True, related_name='bans')
    ip_hash = models.CharField(max_length=64, db_index=True)
    reason = models.TextField(blank=True, default="Rule violation")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['ip_hash', 'expires_at']),
        ]

    def is_active(self):
        if self.expires_at is None:
            return True
        return timezone.now() < self.expires_at

    def __str__(self):
        scope = self.board.uri if self.board else "GLOBAL"
        return f"Ban [{scope}] {self.ip_hash[:10]}... Reason: {self.reason}"
