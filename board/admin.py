from datetime import timedelta
from django import forms
from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.html import format_html
from django.db import transaction
from django.db.models import F

from .conf import DEFAULT_BAN_DURATION_DAYS
from .models import Board, Thread, Post, Ban, Report, BannedFile, WordFilter, AuditLog
from .services import delete_post_media, log_staff_action, move_thread_to_board


class MoveThreadForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    destination_board = forms.ModelChoiceField(
        queryset=Board.objects.all(),
        required=True,
        label="Target Board"
    )


@admin.action(description="Move selected thread(s) to another board...")
def move_thread_admin_action(modeladmin, request, queryset):
    if 'apply' in request.POST:
        form = MoveThreadForm(request.POST)
        if form.is_valid():
            dest_board = form.cleaned_data['destination_board']
            moved_count = 0
            for thread in queryset:
                ok, msg, _ = move_thread_to_board(thread.id, dest_board.uri, request.user)
                if ok:
                    moved_count += 1
                else:
                    modeladmin.message_user(request, f"Thread {thread.id}: {msg}", messages.ERROR)

            if moved_count:
                modeladmin.message_user(request, f"Successfully moved {moved_count} thread(s) to /{dest_board.uri}/.", messages.SUCCESS)
            return None
    else:
        form = MoveThreadForm(initial={'_selected_action': queryset.values_list('pk', flat=True)})

    return render(
        request,
        'admin/board/thread/move_thread_confirm.html',
        context={'threads': queryset, 'form': form}
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'staff_user', 'action', 'board', 'target_post_num', 'short_details')
    list_filter = ('action', 'board', 'created_at', 'staff_user')
    search_fields = ('details', 'target_ip_hash', 'staff_user__username')
    readonly_fields = ('staff_user', 'board', 'action', 'details', 'target_post_num', 'target_ip_hash', 'created_at')
    list_select_related = ('staff_user', 'board')

    def short_details(self, obj):
        return (obj.details[:80] + '...') if len(obj.details) > 80 else obj.details
    short_details.short_description = "Details"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(WordFilter)
class WordFilterAdmin(admin.ModelAdmin):
    list_display = ('pattern', 'replacement', 'match_type', 'action', 'reason', 'is_active', 'created_at')
    list_filter = ('action', 'match_type', 'is_active')
    search_fields = ('pattern', 'replacement', 'reason')
    list_editable = ('replacement', 'action', 'is_active')


@admin.register(BannedFile)
class BannedFileAdmin(admin.ModelAdmin):
    list_display = ('file_hash_short', 'reason', 'staff_user', 'created_at')
    search_fields = ('file_hash', 'reason')
    readonly_fields = ('created_at',)
    list_filter = ('created_at',)
    list_select_related = ('staff_user',)

    def file_hash_short(self, obj):
        return f"{obj.file_hash[:16]}..."
    file_hash_short.short_description = "File Hash (SHA-256)"


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'board_link', 'post_link', 'reason', 'reporter_ip_hash', 'created_at', 'is_cleared')
    list_filter = ('board', 'is_cleared', 'created_at')
    search_fields = ('reason', 'post__content', 'post__post_num')
    actions = ['clear_reports', 'delete_reported_posts']
    list_select_related = ('board', 'post')

    def board_link(self, obj):
        return f"/{obj.board.uri}/"
    board_link.short_description = "Board"

    def post_link(self, obj):
        return format_html(
            '<a href="/admin/board/post/{}/change/">No. {}</a>',
            obj.post.id,
            obj.post.post_num
        )
    post_link.short_description = "Target Post"

    def reporter_ip_hash(self, obj):
        return obj.ip_hash[:12] + "..."
    reporter_ip_hash.short_description = "Reporter IP"

    @admin.action(description="Mark selected reports as cleared")
    def clear_reports(self, request, queryset):
        count = queryset.update(is_cleared=True)
        self.message_user(request, f"Marked {count} report(s) as cleared.")

    @admin.action(description="Delete target posts and clear reports")
    def delete_reported_posts(self, request, queryset):
        deleted_thread_ids = set()
        with transaction.atomic():
            posts = list(queryset.select_related('post', 'post__thread'))
            for r in posts:
                p = r.post
                if p.thread_id in deleted_thread_ids:
                    continue
                thread = p.thread
                if p.is_op:
                    deleted_thread_ids.add(thread.id)
                    for rep in thread.posts.all():
                        delete_post_media(rep)
                    thread.delete()
                else:
                    delete_post_media(p)
                    p.delete()
                    thread.post_count = F('post_count') - 1
                    thread.save(update_fields=['post_count'])
            queryset.update(is_cleared=True)
        self.message_user(request, f"Purged posts and resolved {len(posts)} report(s).")


class MassBanForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    reason = forms.CharField(max_length=255, initial="Rule violation", required=True)
    duration_value = forms.IntegerField(min_value=1, initial=DEFAULT_BAN_DURATION_DAYS, required=False)
    duration_unit = forms.ChoiceField(
        choices=[('hours', 'Hour(s)'), ('days', 'Day(s)'), ('weeks', 'Week(s)')],
        initial='days',
        required=False
    )
    is_permanent = forms.BooleanField(required=False, label="Permanent")
    is_global = forms.BooleanField(required=False, initial=False, label="Global Ban")
    delete_posts = forms.BooleanField(required=False, initial=True, label="Delete Selected Posts")


@admin.action(description="Ban posters of selected posts (custom duration)...")
def mass_ban_action(modeladmin, request, queryset):
    if 'apply' in request.POST:
        form = MassBanForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data['reason']
            is_perm = form.cleaned_data['is_permanent']
            duration_val = form.cleaned_data['duration_value']
            duration_unit = form.cleaned_data['duration_unit']
            is_global = form.cleaned_data['is_global']
            delete_posts = form.cleaned_data['delete_posts']

            if is_perm or not duration_val:
                expires_at = None
            elif duration_unit == 'hours':
                expires_at = timezone.now() + timedelta(hours=duration_val)
            elif duration_unit == 'weeks':
                expires_at = timezone.now() + timedelta(weeks=duration_val)
            else:
                expires_at = timezone.now() + timedelta(days=duration_val)

            bans_created = 0
            deleted_thread_ids = set()
            with transaction.atomic():
                for post in queryset.select_related('board', 'thread'):
                    if post.ip_hash:
                        Ban.objects.create(
                            board=None if is_global else post.board,
                            ip_hash=post.ip_hash,
                            reason=reason,
                            expires_at=expires_at,
                            staff_user=request.user
                        )
                        bans_created += 1

                    if delete_posts:
                        if post.thread_id in deleted_thread_ids:
                            continue
                        thread = post.thread
                        if post.is_op:
                            deleted_thread_ids.add(thread.id)
                            for rep in thread.posts.all():
                                delete_post_media(rep)
                            thread.delete()
                        else:
                            delete_post_media(post)
                            post.delete()
                            thread.post_count = F('post_count') - 1
                            thread.save(update_fields=['post_count'])

            modeladmin.message_user(
                request,
                f"Successfully issued {bans_created} ban(s).",
                messages.SUCCESS
            )
            return None
    else:
        form = MassBanForm(initial={'_selected_action': queryset.values_list('pk', flat=True)})

    return render(
        request,
        'admin/board/post/mass_ban.html',
        context={'posts': queryset, 'form': form}
    )


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ('uri', 'title', 'subtitle', 'bump_limit', 'max_pages', 'last_post_number')
    list_display_links = ('uri', 'title')
    list_editable = ('bump_limit', 'max_pages')
    search_fields = ('uri', 'title')
    ordering = ('uri',)


class PostInline(admin.TabularInline):
    model = Post
    extra = 0
    readonly_fields = ('post_num', 'name', 'tripcode', 'ip_hash', 'created_at', 'thumbnail_preview')
    fields = ('post_num', 'name', 'tripcode', 'content', 'ip_hash', 'thumbnail_preview', 'created_at')
    can_delete = True
    show_change_link = True

    def thumbnail_preview(self, instance):
        if instance.thumbnail:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-height: 50px;" /></a>',
                instance.image.url,
                instance.thumbnail.url,
            )
        return "-"
    thumbnail_preview.short_description = "Media"


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ('id', 'board', 'is_sticky', 'is_locked', 'is_archived', 'post_count', 'last_bumped', 'created_at')
    list_filter = ('board', 'is_sticky', 'is_locked', 'is_archived')
    search_fields = ('id', 'posts__content')
    ordering = ('-is_sticky', '-last_bumped')
    actions = ['toggle_sticky', 'toggle_lock', move_thread_admin_action]
    list_select_related = ('board',)
    inlines = [PostInline]

    @admin.action(description="Toggle sticky status")
    def toggle_sticky(self, request, queryset):
        for thread in queryset:
            thread.is_sticky = not thread.is_sticky
            thread.save(update_fields=['is_sticky'])

    @admin.action(description="Toggle locked status")
    def toggle_lock(self, request, queryset):
        for thread in queryset:
            thread.is_locked = not thread.is_locked
            thread.save(update_fields=['is_locked'])


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'board_link', 'post_num', 'is_op', 'author_display', 'short_content', 'media_preview', 'ip_hash', 'created_at')
    list_filter = ('board', 'is_op', 'created_at')
    search_fields = ('post_num', 'content', 'ip_hash', 'file_hash', 'name', 'tripcode')
    readonly_fields = ('board', 'thread', 'post_num', 'is_op', 'ip_hash', 'file_hash', 'created_at', 'media_preview')
    ordering = ('-created_at',)
    list_select_related = ('board', 'thread')
    actions = [mass_ban_action, 'delete_all_by_same_ip_hash', 'ban_ip_hash_3_days']

    def board_link(self, instance):
        return f"/{instance.board.uri}/"
    board_link.short_description = "Board"

    def author_display(self, instance):
        if instance.tripcode:
            return format_html("<strong>{}</strong> <code>{}</code>", instance.name, instance.tripcode)
        return instance.name
    author_display.short_description = "Author"

    def short_content(self, instance):
        return instance.content[:80] + "..." if len(instance.content) > 80 else instance.content
    short_content.short_description = "Body"

    def media_preview(self, instance):
        if instance.thumbnail:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-height: 60px; max-width: 60px;" /></a>',
                instance.image.url,
                instance.thumbnail.url,
            )
        elif instance.image:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-height: 60px; max-width: 60px;" /></a>',
                instance.image.url,
                instance.image.url,
            )
        return "-"
    media_preview.short_description = "Thumb"

    def delete_model(self, request, obj):
        with transaction.atomic():
            thread = obj.thread
            if obj.is_op:
                for rep in thread.posts.all():
                    delete_post_media(rep)
                thread.delete()
            else:
                delete_post_media(obj)
                obj.delete()
                thread.post_count = F('post_count') - 1
                thread.save(update_fields=['post_count'])

    def delete_queryset(self, request, queryset):
        deleted_thread_ids = set()
        with transaction.atomic():
            for post in queryset.select_related('thread'):
                if post.thread_id in deleted_thread_ids:
                    continue
                thread = post.thread
                if post.is_op:
                    deleted_thread_ids.add(thread.id)
                    for rep in thread.posts.all():
                        delete_post_media(rep)
                    thread.delete()
                else:
                    delete_post_media(post)
                    post.delete()
                    thread.post_count = F('post_count') - 1
                    thread.save(update_fields=['post_count'])

    @admin.action(description="BAN: Issue 3-day global ban to selected poster(s)")
    def ban_ip_hash_3_days(self, request, queryset):
        ip_hashes = set(queryset.exclude(ip_hash="").values_list('ip_hash', flat=True))
        expiry = timezone.now() + timedelta(days=3)
        created_count = 0

        for h in ip_hashes:
            _, created = Ban.objects.get_or_create(
                ip_hash=h,
                board=None,
                defaults={'reason': 'Spam / Rule violation', 'expires_at': expiry}
            )
            if created:
                created_count += 1

        self.message_user(request, f"Applied 3-day global bans to {created_count} IP hash(es).")

    @admin.action(description="PURGE: Delete selected AND all other posts matching their IP hash")
    def delete_all_by_same_ip_hash(self, request, queryset):
        ip_hashes = set(queryset.exclude(ip_hash="").values_list('ip_hash', flat=True))
        if not ip_hashes:
            self.message_user(request, "Selected posts do not contain valid IP hashes.")
            return

        deleted_thread_ids = set()
        with transaction.atomic():
            matching_posts = list(Post.objects.filter(ip_hash__in=ip_hashes).select_related('thread'))
            for post in matching_posts:
                if post.thread_id in deleted_thread_ids:
                    continue
                thread = post.thread
                if post.is_op:
                    deleted_thread_ids.add(thread.id)
                    for rep in thread.posts.all():
                        delete_post_media(rep)
                    thread.delete()
                else:
                    delete_post_media(post)
                    post.delete()
                    thread.post_count = F('post_count') - 1
                    thread.save(update_fields=['post_count'])

        self.message_user(request, f"Purged {len(matching_posts)} posts associated with {len(ip_hashes)} IP hash(es).")


@admin.register(Ban)
class BanAdmin(admin.ModelAdmin):
    list_display = ('ip_hash_short', 'board', 'reason', 'created_at', 'expires_at', 'is_active_status')
    list_filter = ('board', 'created_at')
    search_fields = ('ip_hash', 'reason')
    list_select_related = ('board', 'staff_user')

    def ip_hash_short(self, obj):
        return obj.ip_hash[:12] + "..."
    ip_hash_short.short_description = "IP Hash"

    def is_active_status(self, obj):
        return obj.is_active()
    is_active_status.boolean = True
    is_active_status.short_description = "Active"

    def delete_model(self, request, obj):
        scope_str = f"/{obj.board.uri}/" if obj.board else "Global"
        log_staff_action(
            staff_user=request.user,
            action='remove_ban',
            board=obj.board,
            details=f"Lifted {scope_str} ban early. Original reason: '{obj.reason}'",
            ip_hash=obj.ip_hash,
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for ban in queryset:
            scope_str = f"/{ban.board.uri}/" if ban.board else "Global"
            log_staff_action(
                staff_user=request.user,
                action='remove_ban',
                board=ban.board,
                details=f"Lifted {scope_str} ban early (bulk). Original reason: '{ban.reason}'",
                ip_hash=ban.ip_hash,
            )
        super().delete_queryset(request, queryset)
