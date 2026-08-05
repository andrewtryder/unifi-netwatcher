(function () {
  const typeSelect = document.getElementById("channel-type");
  const pushoverConfig = document.getElementById("pushover-config");
  const webhookConfig = document.getElementById("webhook-config");
  const form = document.getElementById("create-channel-form");
  const configInput = document.getElementById("config_json");
  const pushoverUser = document.getElementById("pushover-user");
  const pushoverToken = document.getElementById("pushover-token");
  const webhookUrl = document.getElementById("webhook-url");
  const webhookMethod = document.getElementById("webhook-method");
  const webhookBody = document.getElementById("webhook-body");
  const webhookBodySection = document.getElementById("webhook-body-section");
  const webhookGetSection = document.getElementById("webhook-get-section");
  const webhookAllowPrivate = document.getElementById("webhook-allow-private");
  if (!form || !typeSelect) return;

  function updateWebhookMethodFields() {
    const isGet = webhookMethod.value === "GET";
    webhookBodySection.classList.toggle("hidden", isGet);
    webhookGetSection.classList.toggle("hidden", !isGet);
    webhookBody.required = !isGet && typeSelect.value === "webhook";
  }

  function updateConfigVisibility() {
    const isPushover = typeSelect.value === "pushover";
    pushoverConfig.classList.toggle("hidden", !isPushover);
    webhookConfig.classList.toggle("hidden", isPushover);
    pushoverUser.required = isPushover;
    pushoverToken.required = isPushover;
    webhookUrl.required = !isPushover;
    updateWebhookMethodFields();
  }

  typeSelect.addEventListener("change", updateConfigVisibility);
  webhookMethod.addEventListener("change", updateWebhookMethodFields);

  document.querySelectorAll("[data-close-modal]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("create-modal")?.classList.add("hidden");
    });
  });
  document.querySelectorAll("[data-open-modal]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("create-modal")?.classList.remove("hidden");
    });
  });

  form.addEventListener("submit", (event) => {
    if (typeSelect.value === "pushover") {
      if (!pushoverUser.value.trim() || !pushoverToken.value.trim()) {
        event.preventDefault();
        alert("Please enter both your Pushover User Key and API Token.");
        return;
      }
      configInput.value = JSON.stringify({
        user: pushoverUser.value.trim(),
        token: pushoverToken.value.trim(),
      });
    } else {
      if (!webhookUrl.value.trim()) {
        event.preventDefault();
        alert("Please enter a webhook URL.");
        return;
      }
      const method = webhookMethod.value;
      const config = {
        url: webhookUrl.value.trim(),
        method: method,
        allow_private_network: !!(webhookAllowPrivate && webhookAllowPrivate.checked),
      };
      if (method !== "GET") {
        const bodyText = webhookBody.value.trim();
        try {
          JSON.parse(bodyText);
        } catch (e) {
          event.preventDefault();
          alert("Request body must be valid JSON. Use {{message}} where the alert text should go.");
          return;
        }
        config.body_template = bodyText;
      }
      configInput.value = JSON.stringify(config);
    }
  });

  updateConfigVisibility();
})();
