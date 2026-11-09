(function () {
    const scanBtn = document.getElementById('scan-button');
    const testBtn = document.getElementById('test-connection-button');

    async function refreshScanStatus() {
        try {
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
        } catch (_) {}
    }

    if (scanBtn) {
        scanBtn.addEventListener('click', async () => {
            const label = scanBtn.querySelector('.scan-label');
            const feedback = document.getElementById('scan-feedback');
            scanBtn.disabled = true;
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

                await refreshScanStatus();
            } catch (_) {
                if (feedback) {
                    feedback.textContent = 'Scan failed: request error.';
                    feedback.classList.remove('hidden', 'text-secondary');
                    feedback.classList.add('text-error');
                }
            } finally {
                scanBtn.disabled = false;
                if (label) label.textContent = 'Trigger Manual Scan';
            }
        });
    }

    if (testBtn) {
        testBtn.addEventListener('click', async () => {
            const label = testBtn.querySelector('.test-label');
            const feedback = document.getElementById('test-feedback');
            testBtn.disabled = true;
            if (label) label.textContent = 'Testing Connection...';
            if (feedback) feedback.classList.add('hidden');

            try {
                const response = await fetch('/api/unifi/test-connection', { method: 'POST' });
                const res = await response.json();
                const msg = res.message || (res.success ? 'Connection test succeeded.' : 'Connection test failed.');
                if (feedback) {
                    feedback.textContent = msg;
                    feedback.classList.remove('hidden', 'text-error', 'text-secondary');
                    feedback.classList.add(res.success ? 'text-secondary' : 'text-error');
                }
                await refreshScanStatus();
            } catch (_) {
                if (feedback) {
                    feedback.textContent = 'Connection test failed: request error.';
                    feedback.classList.remove('hidden', 'text-secondary');
                    feedback.classList.add('text-error');
                }
            } finally {
                testBtn.disabled = false;
                if (label) label.textContent = 'Test UniFi Connection';
            }
        });
    }
})();
