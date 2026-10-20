import os
from django import forms
from PIL import Image

from .models import Post
from .conf import (
    MAX_FILE_SIZE,
    ALLOWED_EXTENSIONS,
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOW_EMPTY_COMMENT_WITH_FILE,
)
from .captcha import is_captcha_required, verify_captcha_response

# Protect against decompression bomb denial-of-service
Image.MAX_IMAGE_PIXELS = 64_000_000


class PostForm(forms.ModelForm):
    name = forms.CharField(
        max_length=75,
        required=False,
        widget=forms.TextInput(attrs={'size': '28', 'placeholder': 'Anonymous'})
    )
    content = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'cols': 48})
    )
    file = forms.FileField(required=False)
    password = forms.CharField(
        max_length=32,
        required=False,
        widget=forms.PasswordInput(attrs={'size': '15', 'placeholder': 'Password'})
    )
    captcha_token = forms.CharField(widget=forms.HiddenInput(), required=False)
    captcha = forms.CharField(
        max_length=8,
        required=False,
        widget=forms.TextInput(attrs={'size': '8', 'autocomplete': 'off'})
    )
    capcode = forms.ChoiceField(
        choices=[('none', 'None'), ('mod', 'Mod'), ('admin', 'Admin')],
        required=False,
        widget=forms.Select(attrs={'style': 'font-size: 8.5pt;'})
    )
    url = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'tabindex': '-1', 'autocomplete': 'off'})
    )
    spoiler = forms.BooleanField(required=False, widget=forms.CheckboxInput())

    class Meta:
        model = Post
        fields = ['name', 'email', 'subject', 'content', 'password']
        widgets = {
            'email': forms.TextInput(attrs={'size': '28'}),
            'subject': forms.TextInput(attrs={'size': '35'}),
        }

    def __init__(self, *args, is_thread: bool = False, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_thread = is_thread
        self.user = user
        self.is_staff = user.is_authenticated and user.is_staff if user else False
        self.is_admin = user.is_authenticated and user.is_superuser if user else False
        self.require_captcha = is_captcha_required(is_thread=is_thread)

        if not self.is_staff:
            self.fields.pop('capcode', None)
        elif not self.is_admin:
            self.fields['capcode'].choices = [('none', 'None'), ('mod', 'Mod')]

    def clean_capcode(self):
        val = self.cleaned_data.get('capcode') or 'none'
        if val == 'admin' and not self.is_admin:
            return 'mod'
        if not self.is_staff:
            return 'none'
        return val

    def clean_url(self):
        url = self.cleaned_data.get('url')
        if url:
            raise forms.ValidationError("Spam detected.")
        return url

    def clean_file(self):
        f = self.cleaned_data.get('file')
        if not f:
            return f

        if f.size > MAX_FILE_SIZE:
            max_mb = MAX_FILE_SIZE // (1024 * 1024)
            raise forms.ValidationError(f"File exceeds maximum allowed size of {max_mb} MB.")

        ext = os.path.splitext(f.name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(f"File type '{ext}' is not permitted.")

        # Synchronous verification of image headers
        if ext in ALLOWED_IMAGE_EXTENSIONS:
            try:
                img = Image.open(f)
                img.verify()
                f.seek(0)
            except Exception:
                raise forms.ValidationError("The uploaded file is not a valid or supported image.")

        return f

    def clean(self):
        cleaned_data = super().clean()

        content = (cleaned_data.get('content') or '').strip()
        uploaded_file = self.files.get('file')

        if not content and not uploaded_file:
            self.add_error('content', 'Please enter a comment or attach a file.')

        if uploaded_file and not content and not ALLOW_EMPTY_COMMENT_WITH_FILE:
            self.add_error('content', 'A comment is required even when attaching a file.')

        if self.is_thread and not uploaded_file:
            self.add_error('file', 'An image or file is required to start a new thread.')

        if self.require_captcha:
            token = cleaned_data.get('captcha_token')
            answer = cleaned_data.get('captcha')
            if not answer or not verify_captcha_response(token, answer):
                self.add_error('captcha', 'Incorrect or expired CAPTCHA.')

        return cleaned_data
