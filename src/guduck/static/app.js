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
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      updatePage(await response.json());
    } catch (error) {
      checkbox.checked = previous;
      enabled.value = String(!previous);
      feedback.textContent = error.message === "speaker_unavailable"
        ? "请先选择可用音箱"
        : "保存失败，请重试";
    } finally {
      checkbox.disabled = false;
    }
  });

  const setupXiaomiSettings = () => {
    const root = document.querySelector("[data-xiaomi-settings]");
    if (!root) return;

    const stateTarget = root.querySelector("[data-xiaomi-state]");
    const errorTarget = root.querySelector("[data-xiaomi-error]");
    const deviceList = root.querySelector("[data-xiaomi-device-list]");
    const authForm = root.querySelector("[data-xiaomi-auth-form]");
    const otpForm = root.querySelector("[data-xiaomi-otp-form]");
    const cancelForm = root.querySelector("[data-xiaomi-cancel-form]");
    const refreshForm = root.querySelector("[data-xiaomi-refresh-form]");
    const bindingsForm = root.querySelector("[data-xiaomi-bindings-form]");
    const confirmPanel = root.querySelector("[data-xiaomi-confirm]");
    const displayNamesInput = root.querySelector("[data-xiaomi-display-names]");
    const initialNode = root.querySelector("[data-xiaomi-initial]");
    let attemptId = "";
    let pollTimer = null;
    let confirmationId = "";

    const stateLabels = {
      unconfigured: "尚未配置",
      authenticating: "正在授权",
      otp_required: "请输入验证码",
      fetching_devices: "正在获取音箱列表",
      devices_ready: "已授权，请选择绑定设备",
      ready: "已授权",
      auth_required: "授权已过期，请重新授权",
      failed: "授权失败",
      cancelled: "授权已取消",
      expired: "授权操作已过期",
    };
    const errorLabels = {
      invalid_credentials: "账号或密码格式无效",
      invalid_otp: "验证码格式无效",
      unknown_auth_attempt: "授权操作已失效，请重新开始",
      otp_not_required: "当前不需要验证码",
      account_unconfigured: "请先授权小米账号",
      auth_required: "授权已过期，请重新授权",
      device_refresh_failed: "刷新失败，请重试",
      auth_failed: "授权失败，请检查账号信息",
      invalid_binding_request: "音箱绑定信息无效",
      unknown_speaker_binding: "音箱绑定已发生变化，请刷新",
    };
    const activeStates = new Set(["authenticating", "otp_required", "fetching_devices"]);

    const setError = (code = "") => {
      errorTarget.textContent = code ? (errorLabels[code] || "操作失败，请重试") : "";
    };

    const request = async (url, options = {}) => {
      const response = await fetch(url, {
        ...options,
        headers: { Accept: "application/json", ...(options.headers || {}) },
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = { detail: "request_failed" };
      }
      if (!response.ok) {
        const failure = new Error(payload.detail || "request_failed");
        failure.payload = payload;
        failure.status = response.status;
        throw failure;
      }
      return payload;
    };

    const stopPolling = () => {
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const setAttemptInputs = (value) => {
      attemptId = value || "";
      root.querySelectorAll("[data-xiaomi-attempt-input]").forEach((input) => {
        input.value = attemptId;
      });
    };

    const mergedSpeakers = (status) => {
      const speakers = new Map();
      for (const binding of status.bindings || []) speakers.set(binding.id, binding);
      for (const device of status.devices || []) {
        const prior = speakers.get(device.id);
        speakers.set(device.id, { ...prior, ...device });
      }
      return [...speakers.values()];
    };

    const renderDevices = (status) => {
      const speakers = mergedSpeakers(status);
      const fragment = document.createDocumentFragment();
      for (const speaker of speakers) {
        const row = document.createElement("article");
        row.className = "xiaomi-device";
        row.dataset.xiaomiDevice = "";
        row.dataset.bindingId = speaker.id;

        const checkLabel = document.createElement("label");
        checkLabel.className = "xiaomi-device-check";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "selected_ids";
        checkbox.value = speaker.id;
        checkbox.checked = Boolean(speaker.checked);
        const minaName = document.createElement("span");
        minaName.textContent = speaker.mina_name || speaker.name || "未命名音箱";
        checkLabel.append(checkbox, minaName);

        const metadata = document.createElement("span");
        metadata.className = "xiaomi-device-meta";
        metadata.textContent = `${speaker.hardware || "未知型号"} · ${speaker.available ? "可用" : "不可用"}`;

        const nameLabel = document.createElement("label");
        const nameHint = document.createElement("span");
        nameHint.className = "sr-only";
        nameHint.textContent = "自定义名称";
        const nameInput = document.createElement("input");
        nameInput.type = "text";
        nameInput.maxLength = 50;
        nameInput.value = speaker.name || speaker.mina_name || "未命名音箱";
        nameInput.dataset.xiaomiDisplayName = "";
        nameLabel.append(nameHint, nameInput);

        const testButton = document.createElement("button");
        testButton.className = "button secondary";
        testButton.type = "button";
        testButton.dataset.xiaomiTest = "";
        testButton.dataset.testUrl = `/api/xiaomi/bindings/${encodeURIComponent(speaker.id)}/test`;
        testButton.textContent = "测试";

        const testResult = document.createElement("span");
        testResult.dataset.xiaomiTestResult = "";
        testResult.setAttribute("role", "status");
        if (speaker.test_status === "success") testResult.textContent = "测试成功";
        if (speaker.test_status === "failure") testResult.textContent = "测试失败";
        row.append(checkLabel, metadata, nameLabel, testButton, testResult);
        fragment.append(row);
      }
      if (speakers.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "授权后可查看全部音箱";
        fragment.append(empty);
      }
      deviceList.replaceChildren(fragment);
    };

    const renderStatus = (status) => {
      stateTarget.textContent = stateLabels[status.state] || "状态未知";
      setAttemptInputs(status.attempt_id || attemptId);
      const active = activeStates.has(status.state);
      otpForm.hidden = status.state !== "otp_required";
      cancelForm.hidden = !active;
      refreshForm.hidden = !["ready", "devices_ready"].includes(status.state);
      bindingsForm.hidden = !["ready", "devices_ready"].includes(status.state);
      const otpLabel = root.querySelector("[data-xiaomi-otp-label]");
      if (otpLabel) {
        otpLabel.textContent = status.otp_method === "Email" ? "邮箱验证码" : "短信验证码";
      }
      renderDevices(status);
      if (!active) stopPolling();
      setError(status.error_code || "");
    };

    const pollStatus = async () => {
      if (!attemptId) return;
      try {
        const status = await request(
          `${root.dataset.statusUrl}?attempt_id=${encodeURIComponent(attemptId)}`,
        );
        renderStatus(status);
      } catch (error) {
        stopPolling();
        setError(error.message);
      }
    };

    const startPolling = () => {
      stopPolling();
      pollTimer = setInterval(pollStatus, 1000);
    };

    const postForm = async (form) => request(form.action, {
      method: "POST",
      body: new FormData(form),
    });

    authForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      setError();
      const password = authForm.querySelector("[name=password]");
      try {
        const payload = await postForm(authForm);
        password.value = "";
        renderStatus(payload);
        if (activeStates.has(payload.state)) startPolling();
      } catch (error) {
        password.value = "";
        setError(error.message);
      }
    });

    otpForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      setError();
      try {
        const payload = await postForm(otpForm);
        otpForm.querySelector("[name=code]").value = "";
        renderStatus(payload);
        if (activeStates.has(payload.state)) startPolling();
      } catch (error) {
        setError(error.message);
      }
    });

    cancelForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      setError();
      try {
        renderStatus(await postForm(cancelForm));
      } catch (error) {
        setError(error.message);
      }
    });

    refreshForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      setError();
      try {
        renderStatus(await postForm(refreshForm));
      } catch (error) {
        setError(error.message);
      }
    });

    const saveBindings = async (confirmed = "") => {
      const names = {};
      root.querySelectorAll("[data-xiaomi-device]").forEach((row) => {
        names[row.dataset.bindingId] = row.querySelector("[data-xiaomi-display-name]").value;
      });
      displayNamesInput.value = JSON.stringify(names);
      const body = new FormData(bindingsForm);
      if (confirmed) body.set("confirmation_id", confirmed);
      return request(bindingsForm.action, { method: "POST", body });
    };

    bindingsForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      setError();
      confirmationId = "";
      confirmPanel.hidden = true;
      try {
        const payload = await saveBindings();
        renderStatus(payload.status);
      } catch (error) {
        if (error.status === 409 && error.payload.detail === "unbind_confirmation_required") {
          confirmationId = error.payload.confirmation_id;
          root.querySelector("[data-xiaomi-affected-cameras]").textContent =
            (error.payload.affected_cameras || []).join("、");
          confirmPanel.hidden = false;
        } else {
          setError(error.message);
        }
      }
    });

    root.querySelector("[data-xiaomi-confirm-cancel]").addEventListener("click", () => {
      confirmationId = "";
      confirmPanel.hidden = true;
    });

    root.querySelector("[data-xiaomi-confirm-save]").addEventListener("click", async () => {
      if (!confirmationId) return;
      setError();
      try {
        const payload = await saveBindings(confirmationId);
        confirmationId = "";
        confirmPanel.hidden = true;
        renderStatus(payload.status);
      } catch (error) {
        setError(error.message);
      }
    });

    root.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-xiaomi-test]");
      if (!button) return;
      button.disabled = true;
      setError();
      try {
        const body = new FormData();
        body.set("csrf_token", root.dataset.csrfToken);
        const payload = await request(button.dataset.testUrl, { method: "POST", body });
        button.closest("[data-xiaomi-device]").querySelector("[data-xiaomi-test-result]").textContent = payload.message;
      } catch (error) {
        setError(error.message);
      } finally {
        button.disabled = false;
      }
    });

    let initialStatus = { state: "unconfigured", devices: [], bindings: [] };
    try {
      initialStatus = JSON.parse(initialNode.textContent);
    } catch (_error) {
      setError("request_failed");
    }
    renderStatus(initialStatus);
    if (activeStates.has(initialStatus.state)) startPolling();
  };

  setupXiaomiSettings();
})();
