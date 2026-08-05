(function () {
    const form = document.getElementById('cidr-form');
    const validateBtn = document.getElementById('validate-cidrs-btn');
    const preview = document.getElementById('cidr-preview-result');
    const lockout = document.getElementById('lockout-controls');
    const enabled = document.getElementById('cidr_restriction_enabled');
    const textarea = document.getElementById('allowed_cidrs');
    if (form && validateBtn) {
        async function runPreview() {
            const body = new FormData();
            body.append('allowed_cidrs', textarea?.value || '');
            if (enabled?.checked) body.append('cidr_restriction_enabled', 'on');
            try {
                const res = await fetch('/security/api/cidr-preview', { method: 'POST', body, credentials: 'same-origin' });
                const data = await res.json();
                if (preview) {
                    const errs = (data.errors || []).map((e) => `<li>${e.replace(/</g,'&lt;')}</li>`).join('');
                    const norms = (data.normalized_cidrs || []).map((c) => `<code class="font-mono">${c.replace(/</g,'&lt;')}</code>`).join(', ') || '—';
                    preview.innerHTML = `
                        <div>Valid: <span class="font-mono">${data.valid ? 'yes' : 'no'}</span></div>
                        <div>Normalized: ${norms}</div>
                        <div>Client remains allowed: <span class="font-mono">${data.client_will_remain_allowed ? 'yes' : 'no'}</span></div>
                        ${errs ? `<ul class="list-disc list-inside text-error">${errs}</ul>` : ''}
                    `;
                }
                if (lockout) {
                    const show = enabled?.checked && data.valid && data.client_will_remain_allowed === false;
                    lockout.classList.toggle('hidden', !show);
                }
            } catch (_) {
                if (preview) preview.textContent = 'Preview request failed.';
            }
        }
        validateBtn.addEventListener('click', (e) => {
            e.preventDefault();
            runPreview();
        });
    }

    const hostsForm = document.getElementById('hosts-form');
    const validateHostsBtn = document.getElementById('validate-hosts-btn');
    const hostsPreview = document.getElementById('hosts-preview-result');
    const hostsLockout = document.getElementById('hosts-lockout-controls');
    const hostsEnabled = document.getElementById('host_restriction_enabled');
    const hostsTextarea = document.getElementById('allowed_hosts');
    if (hostsForm && validateHostsBtn) {
        async function runHostsPreview() {
            const body = new FormData();
            body.append('allowed_hosts', hostsTextarea?.value || '');
            if (hostsEnabled?.checked) body.append('host_restriction_enabled', 'on');
            try {
                const res = await fetch('/security/api/hosts-preview', { method: 'POST', body, credentials: 'same-origin' });
                const data = await res.json();
                if (hostsPreview) {
                    const errs = (data.errors || []).map((e) => `<li>${e.replace(/</g,'&lt;')}</li>`).join('');
                    const norms = (data.normalized_hosts || []).map((c) => `<code class="font-mono">${c.replace(/</g,'&lt;')}</code>`).join(', ') || '—';
                    hostsPreview.innerHTML = `
                        <div>Valid: <span class="font-mono">${data.valid ? 'yes' : 'no'}</span></div>
                        <div>Normalized: ${norms}</div>
                        <div>Host remains allowed: <span class="font-mono">${data.client_will_remain_allowed ? 'yes' : 'no'}</span></div>
                        ${errs ? `<ul class="list-disc list-inside text-error">${errs}</ul>` : ''}
                    `;
                }
                if (hostsLockout) {
                    const show = hostsEnabled?.checked && data.valid && data.client_will_remain_allowed === false;
                    hostsLockout.classList.toggle('hidden', !show);
                }
            } catch (_) {
                if (hostsPreview) hostsPreview.textContent = 'Preview request failed.';
            }
        }
        validateHostsBtn.addEventListener('click', (e) => {
            e.preventDefault();
            runHostsPreview();
        });
    }
})();
