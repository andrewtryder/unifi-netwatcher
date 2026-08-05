(function () {
    const tbody = document.getElementById('inventory-tbody');
    const headers = document.querySelectorAll('.sortable-th');
    let currentKey = null;
    let currentDir = 'asc';

    function getSortValue(row, key) {
        const raw = row.dataset[key] || '';
        if (key === 'satisfaction' || key === 'bandwidth') {
            const n = parseFloat(raw);
            return Number.isFinite(n) ? n : -Infinity;
        }
        return raw.toLowerCase();
    }

    function updateIndicators(activeKey, dir) {
        headers.forEach((btn) => {
            const ind = btn.querySelector('.sort-indicator');
            if (!ind) return;
            if (btn.dataset.sortKey === activeKey) {
                ind.textContent = dir === 'asc' ? 'arrow_upward' : 'arrow_downward';
                ind.classList.remove('opacity-40');
            } else {
                ind.textContent = 'unfold_more';
                ind.classList.add('opacity-40');
            }
        });
    }

    headers.forEach((btn) => {
        btn.addEventListener('click', () => {
            const key = btn.dataset.sortKey;
            if (currentKey === key) {
                currentDir = currentDir === 'asc' ? 'desc' : 'asc';
            } else {
                currentKey = key;
                currentDir = 'asc';
            }
            const rows = Array.from(tbody.querySelectorAll('.inventory-row'));
            rows.sort((a, b) => {
                const av = getSortValue(a, key);
                const bv = getSortValue(b, key);
                let cmp = 0;
                if (av < bv) cmp = -1;
                else if (av > bv) cmp = 1;
                return currentDir === 'asc' ? cmp : -cmp;
            });
            rows.forEach((r) => tbody.appendChild(r));
            updateIndicators(currentKey, currentDir);
        });
    });
})();
