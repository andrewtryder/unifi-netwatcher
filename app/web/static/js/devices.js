(function () {
  async function refreshDeviceFragments(deviceId) {
    const [badgeRes, actionsRes] = await Promise.all([
      fetch(`/htmx/devices/${deviceId}/status-badge`, { credentials: "same-origin" }),
      fetch(`/htmx/devices/${deviceId}/actions`, { credentials: "same-origin" }),
    ]);
    if (badgeRes.ok) {
      const doc = new DOMParser().parseFromString(await badgeRes.text(), "text/html");
      const next = doc.getElementById("device-status-badge");
      const current = document.getElementById("device-status-badge");
      if (next && current) current.replaceWith(next);
    }
    if (actionsRes.ok) {
      const doc = new DOMParser().parseFromString(await actionsRes.text(), "text/html");
      const next = doc.getElementById("device-actions");
      const current = document.getElementById("device-actions");
      if (next && current) {
        current.replaceWith(next);
        bindDeviceActions();
      }
    }
    window.NetWatcher?.refreshNav();
  }

  function bindDeviceActions() {
    document.querySelectorAll("[data-device-action]").forEach((btn) => {
      if (btn.dataset.bound === "true") return;
      btn.dataset.bound = "true";
      btn.addEventListener("click", async () => {
        const action = btn.dataset.deviceAction;
        const deviceId = btn.dataset.deviceId;
        const deviceMac = btn.dataset.deviceMac;

        if (action === "delete") {
          const confirmed = confirm(
            `Permanently delete ${deviceMac}?\n\nThis removes the device and all its observations and history. It may reappear on a future scan if still on your network.`
          );
          if (!confirmed) return;
        }

        btn.disabled = true;
        try {
          await window.NetWatcher.apiFetch(`/api/devices/${deviceId}/${action}`, {
            method: "POST",
          });
          if (action === "delete") {
            window.location.href = "/devices";
            return;
          }
          await refreshDeviceFragments(deviceId);
        } catch (e) {
          alert(action === "delete" ? "Failed to delete device." : "Failed to update device status.");
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  bindDeviceActions();
})();
