document.addEventListener('DOMContentLoaded', () => {
    let preview = null;
    let hoverToken = 0;
    const postCache = new Map();

    function renderPopup(sourceElement, link) {
        if (preview) {
            preview.remove();
            preview = null;
        }

        const postBox = sourceElement.classList.contains('reply') || sourceElement.classList.contains('op')
            ? sourceElement
            : (sourceElement.querySelector('.reply, .op') || sourceElement);

        if (!postBox) return;

        const clone = postBox.cloneNode(true);
        clone.id = '';
        clone.querySelectorAll('.post-backlinks, .mod-controls, input[type="checkbox"]').forEach(el => el.remove());

        const previewWrapper = document.createElement('div');
        previewWrapper.className = 'post-hover-preview';
        previewWrapper.appendChild(clone);
        document.body.appendChild(previewWrapper);

        const lRect = link.getBoundingClientRect();
        const pRect = previewWrapper.getBoundingClientRect();
        const sX = window.scrollX, sY = window.scrollY;

        let left = sX + lRect.left;
        if (left + pRect.width > sX + window.innerWidth - 15) {
            left = sX + window.innerWidth - pRect.width - 15;
        }

        let top = (lRect.top - pRect.height - 6 >= 0)
            ? sY + lRect.top - pRect.height - 6
            : sY + lRect.bottom + 6;

        previewWrapper.style.top = `${Math.max(0, top)}px`;
        previewWrapper.style.left = `${Math.max(10, left)}px`;
        preview = previewWrapper;
    }

    async function fetchRemotePost(url, postNum) {
        const cacheKey = `${url}#p${postNum}`;
        if (postCache.has(cacheKey)) {
            return postCache.get(cacheKey);
        }

        try {
            const resp = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!resp.ok) return null;

            const htmlText = await resp.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(htmlText, 'text/html');

            const postEl = doc.getElementById('p' + postNum);
            if (postEl) {
                postCache.set(cacheKey, postEl);
                return postEl;
            }
        } catch (err) {
            console.error('Failed to fetch remote preview:', err);
        }
        return null;
    }

    // Hover Preview Handler (Local, Cross-Thread, and Cross-Board Quotes)
    document.body.addEventListener('mouseover', async (e) => {
        const link = e.target.closest('a.quotelink, .post-backlinks a');
        if (!link) return;

        const href = link.getAttribute('href');
        if (!href || !href.includes('#p')) return;

        const [targetUrl, targetHash] = href.split('#');
        const postNum = targetHash.replace(/^p/, '');
        if (!postNum) return;

        const currentToken = ++hoverToken;

        const onMouseLeave = () => {
            hoverToken++;
            if (preview) {
                preview.remove();
                preview = null;
            }
            link.removeEventListener('mouseleave', onMouseLeave);
        };
        link.addEventListener('mouseleave', onMouseLeave, { once: true });

        const localTarget = document.getElementById('p' + postNum);
        if (localTarget) {
            renderPopup(localTarget, link);
            return;
        }

        if (targetUrl) {
            const remotePost = await fetchRemotePost(targetUrl, postNum);
            if (remotePost && currentToken === hoverToken && link.matches(':hover')) {
                renderPopup(remotePost, link);
            }
        }
    });

    // Inline Video Closer
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('.close-video-btn');
        const details = btn && btn.closest('details');
        if (!details) return;

        const v = details.querySelector('video');
        if (v) { v.pause(); v.currentTime = 0; }
        details.removeAttribute('open');
    });
});
