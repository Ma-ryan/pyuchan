import io
import json
import os
import subprocess
from django_huey import db_task
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

from .conf import (
    CONVERT_THUMBS_TO_WEBP,
    FFMPEG_PATH,
    FFPROBE_PATH,
    MAX_THUMB_SIZE,
    THUMB_WEBP_QUALITY,
)
from .models import Post

# Protect worker memory from decompression floods
Image.MAX_IMAGE_PIXELS = 64_000_000


def get_video_metadata(filepath: str) -> tuple[int, int]:
    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-protocol_whitelist", "file",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        filepath,
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10, check=True)
        info = json.loads(res.stdout)
        stream = info["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception:
        return 0, 0


@db_task()
def process_post_media_task(post_id: int):
    """
    Background worker task to generate thumbnails and calculate dimensions.
    Enforces strict protocol sandboxing and subprocess timeouts.
    """
    try:
        post = Post.objects.get(id=post_id)
    except Post.DoesNotExist:
        return

    if not post.image or not os.path.isfile(post.image.path):
        return

    if post.is_non_image:
        return

    filepath = post.image.path

    try:
        if post.is_video:
            width, height = get_video_metadata(filepath)
            post.image_width = width
            post.image_height = height

            thumb_filename = f"{post.file_hash[:16]}_thumb.webp"
            thumb_rel_path = os.path.join("thumb", thumb_filename)
            thumb_abs_path = os.path.join(post.image.storage.location, thumb_rel_path)

            os.makedirs(os.path.dirname(thumb_abs_path), exist_ok=True)

            cmd = [
                FFMPEG_PATH,
                "-y",
                "-protocol_whitelist", "file",
                "-ss", "00:00:01",
                "-i", filepath,
                "-vframes", "1",
                "-vf", f"scale='min({MAX_THUMB_SIZE[0]},iw)':'min({MAX_THUMB_SIZE[1]},ih)':force_original_aspect_ratio=decrease",
                thumb_abs_path,
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True)
            post.thumbnail.name = thumb_rel_path

        elif post.is_image:
            with Image.open(filepath) as img:
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass

                post.image_width, post.image_height = img.size

                thumb_img = img.copy()
                if getattr(thumb_img, "is_animated", False):
                    thumb_img.seek(0)

                thumb_img.thumbnail(MAX_THUMB_SIZE, Image.Resampling.LANCZOS)
                thumb_io = io.BytesIO()

                if CONVERT_THUMBS_TO_WEBP:
                    if thumb_img.mode not in ("RGB", "RGBA"):
                        thumb_img = thumb_img.convert("RGBA" if "transparency" in thumb_img.info else "RGB")
                    thumb_img.save(thumb_io, format="WEBP", quality=THUMB_WEBP_QUALITY, method=4)
                    thumb_ext = ".webp"
                else:
                    if thumb_img.mode != "RGB":
                        thumb_img = thumb_img.convert("RGB")
                    thumb_img.save(thumb_io, format="JPEG", quality=85)
                    thumb_ext = ".jpg"

                thumb_filename = f"{post.file_hash[:16]}_thumb{thumb_ext}"
                post.thumbnail.save(thumb_filename, ContentFile(thumb_io.getvalue()), save=False)

        post.save(update_fields=['thumbnail', 'image_width', 'image_height'])

    except Exception:
        # Purge corrupt media on unhandled failure
        if post.image and os.path.isfile(post.image.path):
            try:
                os.remove(post.image.path)
            except OSError:
                pass
        post.image.delete(save=False)
        post.is_file_deleted = True
        post.save(update_fields=['image', 'is_file_deleted'])
