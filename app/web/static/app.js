(function () {
  function formatRelativeAgo(isoTimestamp, parens) {
    const date = new Date(isoTimestamp);
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    let text;
    if (seconds < 60) text = `${seconds}s ago`;
    else {
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) text = `${minutes}m ago`;
      else {
        const hours = Math.floor(minutes / 60);
        const remMin = minutes % 60;
        if (hours < 24) text = remMin ? `${hours}h ${remMin}m ago` : `${hours}h ago`;
        else text = `${Math.floor(hours / 24)}d ago`;
      }
    }
    return parens ? `(${text})` : text;
  }

  function updateRelativeTimes() {
    document.querySelectorAll("[data-relative-time]").forEach((el) => {
      const parens = el.dataset.relativeParens === "true";
      el.textContent = formatRelativeAgo(el.dataset.relativeTime, parens);
    });
  }

    async function refreshNav() {
        try {
            const response = await fetch("/htmx/nav");
            if (!response.ok) return;
            const doc = new DOMParser().parseFromString(await response.text(), "text/html");
            ["desktop-nav-links", "mobile-nav"].forEach((id) => {
                const next = doc.getElementById(id);
                const current = document.getElementById(id);
                if (next && current) current.replaceWith(next);
            });
        } catch (_) {
            /* ignore */
        }
    }

  window.NetWatcher = {
    formatRelativeAgo,
    updateRelativeTimes,
    refreshNav,
    removeUnknownRows(deviceIds) {
      deviceIds.forEach((id) => document.getElementById(`row-${id}`)?.remove());
      const tbody = document.getElementById("unknown-devices-tbody");
      if (tbody && !tbody.querySelector("tr[id^='row-']")) {
        tbody.innerHTML = `
          <tr>
            <td colspan="7" class="p-8 text-center text-on-surface-variant italic">
              <div class="flex flex-col items-center justify-center gap-3">
                <span class="material-symbols-outlined text-4xl text-secondary">verified</span>
                <span>No unknown devices found. Your network is clear.</span>
              </div>
            </td>
          </tr>`;
        document.getElementById("bulk-actions")?.remove();
      }
      refreshNav();
    },
  };

  updateRelativeTimes();
  setInterval(updateRelativeTimes, 60000);
})();
