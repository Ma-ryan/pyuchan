import html
import os
import re
import shutil
import struct
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.hashers import make_password

from board.models import Board, Thread, Post
from board.services import hash_ip

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None


def dec_to_dot(numip):
    try:
        parts = struct.unpack('>8B', struct.pack('>Q', int(numip)))
        return '.'.join(str(x) for x in parts[4:8])
    except Exception:
        return ""


def clean_legacy_comment(raw_comment: str) -> str:
    if not raw_comment:
        return ""

    text = raw_comment
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<span class="unkfunc">(.*?)</span>', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'<a [^>]*>(&gt;&gt;&gt;/[^/]+/(?:&gt;&gt;)?\d+)</a>', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'<a [^>]*>(&gt;&gt;\d+)</a>', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()


def parse_legacy_timestamp(row) -> datetime:
    raw_ts = row.get('timestamp')
    if raw_ts:
        try:
            ts_int = int(float(raw_ts))
            if ts_int > 0:
                if ts_int > 4000000000:
                    ts_int //= 1000
                return datetime.fromtimestamp(ts_int, tz=dt_timezone.utc)
        except (ValueError, TypeError):
            pass

    date_str = str(row.get('date') or "").strip()
    if date_str:
        m = re.match(r'(\d{2})/(\d{2})/(\d{2})\([A-Za-z]+\)(\d{2}):(\d{2})(?::(\d{2}))?', date_str)
        if m:
            yy, mm, dd, hh, minute, ss = m.groups()
            year = 2000 + int(yy) if int(yy) < 70 else 1900 + int(yy)
            sec = int(ss) if ss else 0
            return datetime(year, int(mm), int(dd), int(hh), int(minute), sec, tzinfo=dt_timezone.utc)

        m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?', date_str)
        if m2:
            yr, mm, dd, hh, minute, ss = m2.groups()
            sec = int(ss) if ss else 0
            return datetime(int(yr), int(mm), int(dd), int(hh), int(minute), sec, tzinfo=dt_timezone.utc)

    return timezone.now()


class Command(BaseCommand):
    help = "Migrates a Wakarimasen board directly from a local MariaDB/MySQL database into pyuchan."

    def add_arguments(self, parser):
        parser.add_argument('--board-uri', required=True, help="Target board table / URI (e.g. 'a', 'c', 'touhou')")
        parser.add_argument('--board-title', default=None, help="Board title")
        parser.add_argument('--mysql-db', default='waka_desu', help="MySQL database name")
        parser.add_argument('--mysql-user', default='pyuchan', help="MySQL user")
        parser.add_argument('--mysql-pass', default='pyuchanpass', help="MySQL password")
        parser.add_argument('--legacy-board-dir', required=False, default=None, help="Path to media directory")
        parser.add_argument('--clean', action='store_true', help="Delete existing board before importing")

    def handle(self, *args, **options):
        if pymysql is None:
            self.stderr.write("Please install pymysql: pip install pymysql")
            return

        uri = options['board_uri'].strip().lower()
        title = options['board_title'] or f"/{uri}/"
        db_name = options['mysql_db']
        db_user = options['mysql_user']
        db_pass = options['mysql_pass']
        clean_first = options['clean']

        legacy_dir = Path(options['legacy_board_dir']).resolve() if options['legacy_board_dir'] else None

        self.stdout.write(f"Connecting to MariaDB database '{db_name}'...")
        conn = pymysql.connect(
            host='127.0.0.1',
            user=db_user,
            password=db_pass,
            database=db_name,
            cursorclass=pymysql.cursors.DictCursor
        )
        cur = conn.cursor()

        table_name = uri
        try:
            cur.execute(f"SELECT COUNT(*) as cnt FROM `{table_name}`")
            row_cnt = cur.fetchone()['cnt']
            self.stdout.write(f"Found table `{table_name}` with {row_cnt} total posts.")
        except pymysql.MySQLError as e:
            self.stderr.write(f"Table `{table_name}` not found in database: {e}")
            conn.close()
            return

        with transaction.atomic():
            if clean_first:
                self.stdout.write(f"Purging existing board /{uri}/...")
                Board.objects.filter(uri=uri).delete()

            board, created = Board.objects.get_or_create(
                uri=uri,
                defaults={'title': title, 'bump_limit': 300, 'max_pages': 10}
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created board record /{uri}/ in pyuchan."))

            media_src = Path(settings.MEDIA_ROOT) / 'src'
            media_thumb = Path(settings.MEDIA_ROOT) / 'thumb'
            media_src.mkdir(parents=True, exist_ok=True)
            media_thumb.mkdir(parents=True, exist_ok=True)

            cur.execute(f"SELECT * FROM `{table_name}` WHERE parent = 0 ORDER BY num ASC")
            ops = cur.fetchall()
            self.stdout.write(f"Migrating {len(ops)} threads...")

            max_post_num = 0

            for op_row in ops:
                op_num = op_row['num']
                max_post_num = max(max_post_num, op_num)

                created_dt = parse_legacy_timestamp(op_row)
                lasthit_raw = op_row.get('lasthit')
                if lasthit_raw:
                    try:
                        lasthit_dt = datetime.fromtimestamp(int(float(lasthit_raw)), tz=dt_timezone.utc)
                    except Exception:
                        lasthit_dt = created_dt
                else:
                    lasthit_dt = created_dt

                thread = Thread.objects.create(
                    board=board,
                    is_sticky=bool(op_row.get('stickied', 0)),
                    is_locked=bool(op_row.get('locked', '')),
                    last_bumped=lasthit_dt,
                    created_at=created_dt,
                    post_count=1
                )
                thread.created_at = created_dt
                thread.save(update_fields=['created_at'])

                # Set is_op=True explicitly on thread openers
                self._insert_post(board, thread, op_row, legacy_dir, media_src, media_thumb, is_op=True)

                cur.execute(f"SELECT * FROM `{table_name}` WHERE parent = %s ORDER BY num ASC", (op_num,))
                replies = cur.fetchall()

                for rep_row in replies:
                    max_post_num = max(max_post_num, rep_row['num'])
                    self._insert_post(board, thread, rep_row, legacy_dir, media_src, media_thumb, is_op=False)

                thread.post_count = len(replies) + 1
                thread.save(update_fields=['post_count'])

            board.last_post_number = max(board.last_post_number, max_post_num)
            board.save(update_fields=['last_post_number'])

            # Batch backlink and mention resolution
            self.stdout.write("Generating reverse backlinks and mentions...")
            imported_posts = list(Post.objects.filter(board=board).exclude(content=""))
            board_posts_by_num = {p.post_num: p for p in imported_posts}

            for p in imported_posts:
                # Correct lookbehind (?<![>/])>>(\d+) avoids matching >>>/b/>>123
                local_nums = set(int(n) for n in re.findall(r"(?<![>/])>>(\d+)", p.content))
                if local_nums:
                    targets = [board_posts_by_num[n] for n in local_nums if n in board_posts_by_num]
                    if targets:
                        p.mentions.add(*targets)

                cross_mentions = re.findall(r">>>/([a-zA-Z0-9_-]+)/(?:>>)?(\d+)", p.content)
                for target_uri, target_pnum in cross_mentions:
                    target_post = Post.objects.filter(
                        board__uri=target_uri,
                        post_num=int(target_pnum),
                    ).first()
                    if target_post:
                        p.mentions.add(target_post)

        conn.close()
        self.stdout.write(self.style.SUCCESS(f"Successfully migrated /{uri}/ ({len(ops)} threads, max post No. {max_post_num})."))

    def _insert_post(self, board, thread, row, legacy_dir, media_src, media_thumb, is_op=False):
        ip_dot = dec_to_dot(row['ip']) if row['ip'] else ""
        ip_hash_val = hash_ip(ip_dot) if ip_dot else ""

        image_rel_path = ""
        thumb_rel_path = ""
        file_name = ""

        if legacy_dir and row.get('image'):
            raw_img_name = os.path.basename(row['image'])
            legacy_img = legacy_dir / 'src' / raw_img_name
            if legacy_img.exists():
                dest_file = media_src / raw_img_name
                if not dest_file.exists():
                    shutil.copy2(legacy_img, dest_file)
                image_rel_path = f"src/{raw_img_name}"
                file_name = raw_img_name

        if legacy_dir and row.get('thumbnail') and not str(row['thumbnail']).startswith(('/include/icons/', 'include/icons/')):
            raw_thumb_name = os.path.basename(row['thumbnail'])
            legacy_tn = legacy_dir / 'thumb' / raw_thumb_name
            if legacy_tn.exists():
                dest_tn = media_thumb / raw_thumb_name
                if not dest_tn.exists():
                    shutil.copy2(legacy_tn, dest_tn)
                thumb_rel_path = f"thumb/{raw_thumb_name}"

        created_dt = parse_legacy_timestamp(row)
        raw_pw = row.get('password') or ""
        enc_pw = make_password(raw_pw) if raw_pw else ""

        clean_comment = clean_legacy_comment(row.get('comment') or "")
        clean_name = html.unescape(row.get('name') or "Anonymous")
        clean_subject = html.unescape(row.get('subject') or "")

        post = Post.objects.create(
            board=board,
            thread=thread,
            post_num=row['num'],
            is_op=is_op,
            name=clean_name,
            tripcode=row.get('trip') or "",
            email=row.get('email') or "",
            subject=clean_subject,
            content=clean_comment,
            password=enc_pw,
            ip_hash=ip_hash_val,
            image=image_rel_path or None,
            thumbnail=thumb_rel_path or None,
            file_name=file_name,
            file_hash=row.get('md5') or "",
            file_size=row.get('size') or 0,
            image_width=row.get('width') or None,
            image_height=row.get('height') or None,
            created_at=created_dt
        )
        post.created_at = created_dt
        post.save(update_fields=['created_at'])
