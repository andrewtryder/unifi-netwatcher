(function () {
    const selectAll = document.getElementById('select-all');
    const checkboxes = () => Array.from(document.querySelectorAll('.device-select'));
    const bulkTrustBtn = document.getElementById('bulk-trust-btn');
    const bulkIgnoreBtn = document.getElementById('bulk-ignore-btn');
    const bulkTrustCount = document.getElementById('bulk-trust-count');

    function selectedIds() {
        return checkboxes().filter(cb => cb.checked).map(cb => Number(cb.value));
    }

    function updateBulkState() {
        const ids = selectedIds();
        const count = ids.length;
        const allChecked = count > 0 && count === checkboxes().length;
        selectAll.checked = allChecked;
        selectAll.indeterminate = count > 0 && !allChecked;
        bulkTrustBtn.disabled = count === 0;
        bulkIgnoreBtn.disabled = count === 0;
        bulkTrustCount.textContent = count > 0 ? `(${count})` : '';
        checkboxes().forEach(cb => {
            const row = document.getElementById(`row-${cb.value}`);
            if (row) row.classList.toggle('bg-primary/5', cb.checked);
        });
    }

    selectAll.addEventListener('change', () => {
        checkboxes().forEach(cb => { cb.checked = selectAll.checked; });
        updateBulkState();
    });

    checkboxes().forEach(cb => cb.addEventListener('change', updateBulkState));

    async function deviceAction(path, ids) {
        const response = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_ids: ids }),
        });
        if (!response.ok) throw new Error('Request failed');
        return response.json();
    }

    async function singleAction(action, deviceId) {
        await deviceAction(`/api/devices/bulk/${action}`, [deviceId]);
        window.NetWatcher?.removeUnknownRows([deviceId]);
    }

    bulkTrustBtn.addEventListener('click', async () => {
        const ids = selectedIds();
        if (!ids.length) return;
        bulkTrustBtn.disabled = true;
        try {
            await deviceAction('/api/devices/bulk/trust', ids);
            window.NetWatcher?.removeUnknownRows(ids);
        } finally {
            bulkTrustBtn.disabled = false;
        }
    });

    bulkIgnoreBtn.addEventListener('click', async () => {
        const ids = selectedIds();
        if (!ids.length) return;
        bulkIgnoreBtn.disabled = true;
        try {
            await deviceAction('/api/devices/bulk/ignore', ids);
            window.NetWatcher?.removeUnknownRows(ids);
        } finally {
            bulkIgnoreBtn.disabled = false;
        }
    });

    document.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', () => singleAction(btn.dataset.action, Number(btn.dataset.deviceId)));
    });

    updateBulkState();
})();
