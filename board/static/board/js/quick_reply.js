document.addEventListener('DOMContentLoaded', () => {
    let qrBox = null;
    let isDragging = false;
    let dragOffsetX = 0;
    let dragOffsetY = 0;

    function getCookie(name) {
        const val = `; ${document.cookie}`;
        const parts = val.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return '';
    }

    function createQuickReply() {
        if (qrBox) return qrBox;

        const isThreadView = !!document.getElementById('thread-replies');
        const formAction = isThreadView ? '' : window.location.pathname;

        const box = document.createElement('div');
        box.id = 'qr-box';

        box.innerHTML = `
            <div id="qr-header">
                <span id="qr-title">${isThreadView ? 'Quick Reply' : 'New Thread'}</span>
                <span class="qr-close" title="Close">&times;</span>
            </div>
            <div id="qr-body">
                <form method="post" action="${formAction}" enctype="multipart/form-data">
                    <input type="hidden" name="csrfmiddlewaretoken" value="">
                    <input type="text" name="name" placeholder="Name">
                    <input type="text" name="email" placeholder="Email">
                    <input type="text" name="subject" placeholder="Subject">
                    <input type="text" name="url" tabindex="-1" autocomplete="off" style="display:none;" aria-hidden="true">
                    <textarea name="content" placeholder="Comment" rows="4"></textarea>

                    <div id="qr-captcha-row" style="display: none; margin-bottom: 4px;">
                        <input type="hidden" name="captcha_token" value="">
                        <input type="text" name="captcha" id="qr-captcha-input" placeholder="CAPTCHA" size="8" style="width: 80px; display: inline-block;">
                        <img src="" class="captcha-img" alt="CAPTCHA" style="vertical-align: middle; height: 32px; width: auto; cursor: pointer; border: 1px solid #aaa;" onclick="refreshCaptcha()">
                    </div>

                    <div class="qr-file-row">
                        <input type="file" name="file">
                        <label style="font-size: 8pt; margin-left: 2px; user-select: none;">
                            <input type="checkbox" name="spoiler"> Spoiler
                        </label>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
                        <input type="password" name="password" placeholder="Password" style="width: 130px; margin-bottom: 0;">
                        <input type="submit" value="${isThreadView ? 'Reply' : 'New Thread'}" style="margin-bottom: 0;">
                    </div>
                </form>
            </div>
        `;

        const mainCsrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (mainCsrf) {
            box.querySelector('input[name="csrfmiddlewaretoken"]').value = mainCsrf.value;
        }

        // Staff-only capcode select: mirror it from the main form so Quick
        // Reply has full feature parity for moderators/admins.
        const mainCapcode = document.querySelector('select[name="capcode"]');
        if (mainCapcode) {
            const capcodeClone = mainCapcode.cloneNode(true);
            capcodeClone.removeAttribute('id');
            box.querySelector('#qr-body form').insertBefore(capcodeClone, box.querySelector('input[name="name"]'));
        }

        const savedPass = getCookie('post_password');
        if (savedPass) {
            try {
                box.querySelector('input[name="password"]').value = decodeURIComponent(savedPass);
            } catch (e) {}
        }

        const mainCaptchaToken = document.querySelector('input[name="captcha_token"]');
        const mainCaptchaImg = document.querySelector('.captcha-img');
        if (mainCaptchaToken && mainCaptchaToken.value) {
            const qrRow = box.querySelector('#qr-captcha-row');
            qrRow.style.display = 'block';
            box.querySelector('input[name="captcha_token"]').value = mainCaptchaToken.value;
            if (mainCaptchaImg) {
                box.querySelector('.captcha-img').src = mainCaptchaImg.src;
            }
        }

        box.querySelector('.qr-close').addEventListener('click', () => {
            box.style.display = 'none';
        });

        const header = box.querySelector('#qr-header');
        header.addEventListener('mousedown', (e) => {
            if (e.target.classList.contains('qr-close')) return;
            isDragging = true;
            dragOffsetX = e.clientX - box.offsetLeft;
            dragOffsetY = e.clientY - box.offsetTop;
            document.body.style.userSelect = 'none';
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            box.style.left = `${Math.max(10, e.clientX - dragOffsetX)}px`;
            box.style.top = `${Math.max(10, e.clientY - dragOffsetY)}px`;
            box.style.right = 'auto';
        });

        document.addEventListener('mouseup', () => {
            isDragging = false;
            document.body.style.userSelect = '';
        });

        document.body.appendChild(box);
        qrBox = box;
        return qrBox;
    }

    document.addEventListener('click', (e) => {
        const postNumLink = e.target.closest('.post-num');
        if (!postNumLink) return;

        const isThreadView = !!document.getElementById('thread-replies');

        // Guard: Do not open Quick Reply if not in a thread, or if posting is disabled (locked/archived)
        if (!isThreadView || !document.getElementById('post-box')) {
            return;
        }

        e.preventDefault();
        const box = createQuickReply();
        box.style.display = 'block';

        const postNum = postNumLink.getAttribute('data-num') || postNumLink.textContent.replace(/\D/g, '');
        const textarea = box.querySelector('textarea[name="content"]');

        const sel = window.getSelection().toString().trim();
        let quote = `>>${postNum}\n`;
        if (sel) {
            quote += sel.split('\n').map(l => `>${l}`).join('\n') + '\n';
        }

        if (textarea.value && !textarea.value.endsWith('\n')) {
            textarea.value += '\n';
        }
        textarea.value += quote;
        textarea.focus();
        textarea.selectionStart = textarea.selectionEnd = textarea.value.length;
    });
});
