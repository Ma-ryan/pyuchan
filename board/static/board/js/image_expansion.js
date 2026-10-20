document.addEventListener('DOMContentLoaded', () => {
    // Target anchor links containing thumbnails in both OP and replies
    document.body.addEventListener('click', (e) => {
        // Allow middle clicks (auxclick), Ctrl+click, Meta+click, Shift+click to behave natively
        if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) {
            return;
        }

        const link = e.target.closest('.op-media a, .reply-media a');
        if (!link) return;

        // Ignore pure video expanders or links without an image child
        const img = link.querySelector('img');
        if (!img) return;

        const fullSrc = link.getAttribute('href');
        if (!fullSrc) return;

        // Skip expansion for videos, audio files, archives, and documents
        const isExpandableImage = /\.(jpe?g|png|gif|webp)$/i.test(fullSrc);
        if (!isExpandableImage) {
            return; // Allow native browser link navigation / download
        }

        // Prevent opening the URL and expand inline
        e.preventDefault();

        // Skip if target file is a video (handled by details/inline-video)
        const isVideo = /\.(webm|mp4)$/i.test(fullSrc);
        if (isVideo) return;

        // Prevent opening the URL
        e.preventDefault();

        // Toggle state
        const isExpanded = img.classList.contains('inline-expanded');

        if (isExpanded) {
            // Collapse: restore original thumbnail
            const origSrc = img.dataset.thumbSrc;
            if (origSrc) {
                img.src = origSrc;
            }
            img.classList.remove('inline-expanded');
            link.classList.remove('expanded-parent');
        } else {
            // Expand: store thumbnail source and swap with full image
            if (!img.dataset.thumbSrc) {
                img.dataset.thumbSrc = img.src;
            }
            img.src = fullSrc;
            img.classList.add('inline-expanded');
            link.classList.add('expanded-parent');
        }
    });
});
