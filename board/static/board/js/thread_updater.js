document.addEventListener('DOMContentLoaded', () => {
    const threadReplies = document.getElementById('thread-replies');
    const updateBtn = document.getElementById('update-thread-btn');
    const autoCheckbox = document.getElementById('auto-update-cb');
    const timerSpan = document.getElementById('update-timer');
    const notifyCheckbox = document.getElementById('notify-cb');

    if (!threadReplies || !updateBtn) return;

    let countdown = 10;
    let timerInterval = null;
    let isFetching = false;

    function getLastPostNum() {
        const posts = document.querySelectorAll('.post-num');
        if (!posts.length) return 0;
        let maxNum = 0;
        posts.forEach(p => {
            const num = parseInt(p.textContent.replace(/\D/g, ''), 10);
            if (num > maxNum) maxNum = num;
        });
        return maxNum;
    }

    async function fetchUpdates() {
        if (isFetching) return;
        isFetching = true;

        const lastNum = getLastPostNum();
        const url = `${window.location.pathname.replace(/\/$/, '')}/updates/?after=${lastNum}`;

        try {
            const resp = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

            const data = await resp.json();
            if (data.count > 0 && data.html) {
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = data.html;

                const newNodes = Array.from(tempDiv.children);
                newNodes.forEach(node => {
                    node.querySelector('.reply')?.classList.add('new-reply');
                    threadReplies.appendChild(node);
                });

                if (typeof localizeAllTimestamps === 'function') {
                    localizeAllTimestamps(threadReplies);
                }

                if (notifyCheckbox && notifyCheckbox.checked && Notification.permission === 'granted') {
                    new Notification('New reply on pyuchan', {
                        body: `Received ${data.count} new post(s).`,
                        icon: '/static/board/img/spoiler.png'
                    });
                }
            }

            if (data.is_locked) {
                const notice = document.querySelector('.thread-locked-notice');
                if (!notice) {
                    const postBox = document.getElementById('post-box');
                    if (postBox) {
                        postBox.innerHTML = '<div style="text-align: center; font-weight: bold; color: #af0a0f; margin: 1em 0;" class="thread-locked-notice">This thread is locked. You cannot post a reply.</div>';
                    }
                }
            }
        } catch (err) {
            console.error('Thread updater error:', err);
        } finally {
            isFetching = false;
            countdown = 10;
        }
    }

    updateBtn.addEventListener('click', (e) => {
        e.preventDefault();
        fetchUpdates();
    });

    if (notifyCheckbox) {
        notifyCheckbox.addEventListener('change', () => {
            if (notifyCheckbox.checked && Notification.permission !== 'granted') {
                Notification.requestPermission();
            }
        });
    }

    timerInterval = setInterval(() => {
        if (autoCheckbox && autoCheckbox.checked) {
            countdown--;
            if (timerSpan) timerSpan.textContent = `(${countdown})`;
            if (countdown <= 0) {
                fetchUpdates();
            }
        } else {
            if (timerSpan) timerSpan.textContent = '';
        }
    }, 1000);
});
