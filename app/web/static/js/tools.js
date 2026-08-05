(function () {
    const input = document.getElementById('trusted-import-file');
    const filename = document.getElementById('trusted-import-filename');
    if (input && filename) {
        input.addEventListener('change', () => {
            filename.textContent = input.files?.[0]?.name || 'No file chosen';
            filename.classList.toggle('italic', !input.files?.[0]);
        });
    }

    function bindCustomToggle(modeId, wrapId) {
        const mode = document.getElementById(modeId);
        const customWrap = document.getElementById(wrapId);
        function syncCustom() {
            if (!mode || !customWrap) return;
            customWrap.classList.toggle('hidden', mode.value !== 'custom');
        }
        if (mode) {
            mode.addEventListener('change', syncCustom);
            syncCustom();
        }
    }
    bindCustomToggle('interval_mode', 'custom-interval-wrap');
    bindCustomToggle('observation_mode', 'observation-custom-wrap');
    bindCustomToggle('event_mode', 'event-custom-wrap');
})();
