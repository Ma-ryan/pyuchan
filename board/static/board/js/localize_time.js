function formatLocalDate(epochMs) {
    const d = new Date(epochMs);
    const pad = (n) => String(n).padStart(2, '0');

    const yy = String(d.getFullYear()).slice(-2);
    const mm = pad(d.getMonth() + 1);
    const dd = pad(d.getDate());

    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const dayName = days[d.getDay()];

    const hh = pad(d.getHours());
    const min = pad(d.getMinutes());
    const ss = pad(d.getSeconds());

    return `${yy}/${mm}/${dd}(${dayName})${hh}:${min}:${ss}`;
}

function localizeAllTimestamps(root = document) {
    const dateElements = root.querySelectorAll('.post-date[data-utc]');
    dateElements.forEach(el => {
        const utcMs = parseInt(el.getAttribute('data-utc'), 10);
        if (!isNaN(utcMs)) {
            const formatted = formatLocalDate(utcMs);
            // Only update DOM if the text actually differs (prevents unnecessary repaints)
            if (el.textContent !== formatted) {
                el.textContent = formatted;
            }
        }
    });
}

// Convert any dynamic posts after DOM loads
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => localizeAllTimestamps());
} else {
    localizeAllTimestamps();
}
