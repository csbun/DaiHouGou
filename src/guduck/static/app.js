(() => {
  const statusText = (camera, key) => {
    if (key === "stream" && camera.rule_enabled === false) {
      return "未启动（规则关闭）";
    }
    return camera[key];
  };

  const updatePage = (payload) => {
    const camera = payload.camera;
    const row = [...document.querySelectorAll("[data-camera-row]")].find(
      (candidate) => candidate.dataset.cameraId === camera.stream_id,
    );
    if (!row) return;

    const states = {
      stream: statusText(camera, "stream"),
      presence: camera.presence,
      detector: camera.detector || (camera.rules || []).map((rule) => rule.status).join(", "),
    };
    for (const [key, value] of Object.entries(states)) {
      const target = row.querySelector(`[data-camera-state="${key}"]`);
      if (target) target.textContent = value || "stopped";
    }
    if (camera.rules) {
      for (const rule of camera.rules) {
        const form = row.querySelector(`[data-rule-switch][data-rule-id="${CSS.escape(rule.id)}"]`);
        if (!form) continue;
        const checkbox = form.querySelector("[data-rule-checkbox]");
        checkbox.checked = rule.enabled;
        form.querySelector("[data-rule-enabled]").value = String(!rule.enabled);
        const label = checkbox.closest("label").querySelector(".sr-only");
        if (label) label.textContent = `${rule.enabled ? "关闭" : "开启"} ${camera.stream_id} ${rule.name}`;
        const state = form.querySelector(".rule-state");
        if (state) state.textContent = rule.status;
      }
    } else {
      const checkbox = row.querySelector("[data-rule-checkbox]");
      checkbox.checked = camera.rule_enabled;
      checkbox.closest("label").querySelector(".sr-only").textContent =
        `${camera.rule_enabled ? "关闭" : "开启"} ${camera.stream_id} 欢迎规则`;
      row.querySelector("[data-rule-enabled]").value = String(!camera.rule_enabled);
    }

    document.querySelector("[data-enabled-camera-count]").textContent =
      payload.enabled_camera_count;
    document.querySelector("[data-ready-camera-count]").textContent =
      payload.ready_camera_count;
    const notice = document.querySelector("[data-resource-notice]");
    notice.hidden = !payload.resource_warning;
    notice.textContent =
      `已开启 ${payload.enabled_camera_count} 路摄像头，当前服务器负载可能升高；仍可继续开启。`;
  };

  document.addEventListener("change", async (event) => {
    const checkbox = event.target.closest("[data-rule-checkbox]");
    if (!checkbox) return;

    const form = checkbox.closest("[data-rule-switch]");
    const enabled = form.querySelector("[data-rule-enabled]");
    const feedback = form.querySelector(".rule-feedback");
    const previous = !checkbox.checked;
    enabled.value = String(checkbox.checked);
    const ruleId = form.querySelector("[name=rule_id]");
    if (ruleId) ruleId.value = form.dataset.ruleId || checkbox.dataset.ruleId || "";
    checkbox.disabled = true;
    feedback.textContent = "";

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      updatePage(await response.json());
    } catch (error) {
      checkbox.checked = previous;
      enabled.value = String(!previous);
      feedback.textContent = "保存失败，请重试";
    } finally {
      checkbox.disabled = false;
    }
  });
})();
