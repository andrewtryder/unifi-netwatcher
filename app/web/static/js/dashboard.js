(function () {
    const btn = document.getElementById('scan-button');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const label = btn.querySelector('.scan-label');
        const feedback = document.getElementById('scan-feedback');
        btn.disabled = true;
        if (label) label.textContent = 'Scanning...';

        try {
            const response = await fetch('/api/scan/run', { method: 'POST' });
            const res = await response.json();
            const msg = res.success
                ? `Scan complete: ${res.devices_processed} device(s) processed.`
                : `Scan failed: ${res.message}`;
            if (feedback) {
                feedback.textContent = msg;
                feedback.classList.remove('hidden', 'text-error', 'text-secondary');
                feedback.classList.add(res.success ? 'text-secondary' : 'text-error');
            }

            const statusRes = await fetch('/htmx/scan-status');
            if (statusRes.ok) {
                const html = await statusRes.text();
                const doc = new DOMParser().parseFromString(html, 'text/html');
                const panel = doc.getElementById('scan-status-panel');
                const current = document.getElementById('scan-status-panel');
                if (panel && current) {
                    current.replaceWith(panel);
                    window.NetWatcher?.updateRelativeTimes();
                }
            }
        } catch (_) {
            if (feedback) {
                feedback.textContent = 'Scan failed: request error.';
                feedback.classList.remove('hidden', 'text-secondary');
                feedback.classList.add('text-error');
            }
        } finally {
            btn.disabled = false;
            if (label) label.textContent = 'Trigger Manual Scan';
        }
    });
})();
