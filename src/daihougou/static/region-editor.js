(function initializeModule(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.DetectionRegionEditor = api;
  }
})(typeof window !== "undefined" ? window : null, function buildModule() {
  "use strict";

  const MIN_SIZE = 0.02;
  const FULL_FRAME_REGION = Object.freeze({ x: 0, y: 0, width: 1, height: 1 });

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function roundSix(value) {
    const rounded = Math.round((value + Number.EPSILON) * 1000000) / 1000000;
    return Object.is(rounded, -0) ? 0 : rounded;
  }

  function normalizeRegion(region) {
    return {
      x: roundSix(Number(region.x)),
      y: roundSix(Number(region.y)),
      width: roundSix(Number(region.width)),
      height: roundSix(Number(region.height)),
    };
  }

  function validateRegion(region) {
    const normalized = normalizeRegion(region);
    const values = Object.values(normalized);
    const valid =
      values.every(Number.isFinite) &&
      normalized.x >= 0 &&
      normalized.y >= 0 &&
      normalized.width >= MIN_SIZE &&
      normalized.height >= MIN_SIZE &&
      normalized.x + normalized.width <= 1 &&
      normalized.y + normalized.height <= 1;
    return {
      valid,
      region: normalized,
      error: valid ? "" : "请输入画面范围内且宽高不少于 2% 的区域",
    };
  }

  function boundedAxis(startValue, endValue) {
    const start = clamp(Number(startValue), 0, 1);
    const end = clamp(Number(endValue), 0, 1);
    if (Math.abs(end - start) >= MIN_SIZE) {
      return [Math.min(start, end), Math.max(start, end)];
    }
    if (end >= start) {
      const high = Math.min(1, start + MIN_SIZE);
      return [Math.max(0, high - MIN_SIZE), high];
    }
    const low = Math.max(0, start - MIN_SIZE);
    return [low, Math.min(1, low + MIN_SIZE)];
  }

  function drawRegion(start, end) {
    const [left, right] = boundedAxis(start.x, end.x);
    const [top, bottom] = boundedAxis(start.y, end.y);
    return normalizeRegion({
      x: left,
      y: top,
      width: right - left,
      height: bottom - top,
    });
  }

  function moveRegion(region, dx, dy) {
    const normalized = normalizeRegion(region);
    return normalizeRegion({
      x: clamp(normalized.x + Number(dx), 0, 1 - normalized.width),
      y: clamp(normalized.y + Number(dy), 0, 1 - normalized.height),
      width: normalized.width,
      height: normalized.height,
    });
  }

  function resizeRegion(region, handle, dx, dy) {
    const normalized = normalizeRegion(region);
    let left = normalized.x;
    let top = normalized.y;
    let right = normalized.x + normalized.width;
    let bottom = normalized.y + normalized.height;
    const horizontal = Number(dx);
    const vertical = Number(dy);

    if (handle.includes("w")) left = clamp(left + horizontal, 0, right - MIN_SIZE);
    if (handle.includes("e")) right = clamp(right + horizontal, left + MIN_SIZE, 1);
    if (handle.includes("n")) top = clamp(top + vertical, 0, bottom - MIN_SIZE);
    if (handle.includes("s")) bottom = clamp(bottom + vertical, top + MIN_SIZE, 1);

    return normalizeRegion({
      x: left,
      y: top,
      width: right - left,
      height: bottom - top,
    });
  }

  function isFullFrame(region) {
    const normalized = normalizeRegion(region);
    return (
      normalized.x === 0 &&
      normalized.y === 0 &&
      normalized.width === 1 &&
      normalized.height === 1
    );
  }

  function initializeEditor(editor) {
    const config = editor.querySelector(".region-editor-config");
    const preview = editor.querySelector("[data-region-preview]");
    const image = editor.querySelector("[data-region-image]");
    const selection = editor.querySelector("[data-region-selection]");
    const form = editor.querySelector("[data-region-form]");
    const refreshButton = editor.querySelector("[data-refresh-preview]");
    const resetButton = editor.querySelector("[data-reset-region]");
    const saveButton = editor.querySelector("[data-save-region]");
    const previewStatus = editor.querySelector("[data-preview-status]");
    const saveStatus = editor.querySelector("[data-save-status]");
    const errorElement = editor.querySelector("[data-region-error]");
    const visibleInputs = Object.fromEntries(
      Array.from(editor.querySelectorAll("[data-region-input]")).map((input) => [
        input.dataset.regionInput,
        input,
      ]),
    );
    const hiddenInputs = Object.fromEntries(
      Array.from(editor.querySelectorAll("[data-region-value]")).map((input) => [
        input.dataset.regionValue,
        input,
      ]),
    );
    const fields = ["x", "y", "width", "height"];
    let region = normalizeRegion(
      Object.fromEntries(fields.map((field) => [field, hiddenInputs[field].value])),
    );
    let fieldDraft = { ...region };
    let editable = false;
    let previewUrl = null;
    let interaction = null;
    let snapshotRequest = 0;

    function showError(message) {
      errorElement.textContent = message;
      errorElement.hidden = !message;
    }

    function updateSelection() {
      selection.style.left = `${region.x * 100}%`;
      selection.style.top = `${region.y * 100}%`;
      selection.style.width = `${region.width * 100}%`;
      selection.style.height = `${region.height * 100}%`;
    }

    function syncHiddenFields() {
      for (const field of fields) {
        hiddenInputs[field].value = region[field].toFixed(6);
      }
    }

    function syncVisibleFields() {
      for (const field of fields) {
        visibleInputs[field].value = (region[field] * 100).toFixed(2);
      }
    }

    function updateSaveAvailability() {
      const validation = validateRegion(fieldDraft);
      saveButton.disabled = !validation.valid || (!editable && !isFullFrame(fieldDraft));
      return validation;
    }

    function setRegion(nextRegion, options = {}) {
      region = normalizeRegion(nextRegion);
      fieldDraft = { ...region };
      updateSelection();
      syncHiddenFields();
      if (options.syncVisible !== false) syncVisibleFields();
      showError("");
      updateSaveAvailability();
    }

    function setEditingEnabled(enabled) {
      editable = enabled;
      preview.classList.toggle("is-locked", !enabled);
      preview.setAttribute("aria-busy", "false");
      for (const input of Object.values(visibleInputs)) input.disabled = !enabled;
      updateSaveAvailability();
    }

    function pointFromEvent(event) {
      const bounds = image.getBoundingClientRect();
      return {
        x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
        y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1),
      };
    }

    function discardPreview() {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
      image.removeAttribute("src");
      image.hidden = true;
      selection.hidden = true;
      preview.classList.remove("has-image");
      setEditingEnabled(false);
    }

    async function loadSnapshot() {
      const requestId = ++snapshotRequest;
      refreshButton.disabled = true;
      preview.setAttribute("aria-busy", "true");
      previewStatus.textContent = "正在获取画面";
      showError("");
      const body = new FormData();
      body.set("csrf_token", config.dataset.csrfToken);
      body.set("camera_id", editor.dataset.cameraId);
      try {
        const response = await fetch(config.dataset.snapshotUrl, {
          method: "POST",
          body,
          headers: { Accept: "image/jpeg" },
        });
        if (!response.ok) throw new Error("snapshot unavailable");
        const blob = await response.blob();
        if (requestId !== snapshotRequest) return;
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(blob);
        image.onload = () => {
          if (requestId !== snapshotRequest) return;
          image.hidden = false;
          selection.hidden = false;
          preview.classList.add("has-image");
          setEditingEnabled(true);
          updateSelection();
          previewStatus.textContent = "画面已更新";
        };
        image.onerror = () => {
          if (requestId !== snapshotRequest) return;
          discardPreview();
          previewStatus.textContent = "画面暂不可用";
          showError("无法获取当前画面，请重试或恢复全画面");
        };
        image.src = previewUrl;
      } catch (_error) {
        if (requestId !== snapshotRequest) return;
        discardPreview();
        previewStatus.textContent = "画面暂不可用";
        showError("无法获取当前画面，请重试或恢复全画面");
      } finally {
        if (requestId === snapshotRequest) refreshButton.disabled = false;
      }
    }

    for (const [field, input] of Object.entries(visibleInputs)) {
      input.addEventListener("input", () => {
        fieldDraft[field] = Number(input.value) / 100;
        const validation = validateRegion(fieldDraft);
        if (!validation.valid) {
          showError(validation.error);
          saveButton.disabled = true;
          return;
        }
        setRegion(validation.region, { syncVisible: false });
      });
    }

    preview.addEventListener("pointerdown", (event) => {
      if (!editable || image.hidden) return;
      const handle = event.target.closest("[data-region-handle]");
      const inSelection = event.target.closest("[data-region-selection]");
      const start = pointFromEvent(event);
      interaction = {
        mode: handle
          ? "resize"
          : inSelection && !isFullFrame(region)
            ? "move"
            : "draw",
        handle: handle ? handle.dataset.regionHandle : "",
        start,
        initial: { ...region },
      };
      preview.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    preview.addEventListener("pointermove", (event) => {
      if (!interaction) return;
      const current = pointFromEvent(event);
      if (interaction.mode === "draw") {
        setRegion(drawRegion(interaction.start, current));
      } else if (interaction.mode === "move") {
        setRegion(
          moveRegion(
            interaction.initial,
            current.x - interaction.start.x,
            current.y - interaction.start.y,
          ),
        );
      } else {
        setRegion(
          resizeRegion(
            interaction.initial,
            interaction.handle,
            current.x - interaction.start.x,
            current.y - interaction.start.y,
          ),
        );
      }
    });

    function finishInteraction(event) {
      if (!interaction) return;
      if (preview.hasPointerCapture(event.pointerId)) {
        preview.releasePointerCapture(event.pointerId);
      }
      interaction = null;
    }

    preview.addEventListener("pointerup", finishInteraction);
    preview.addEventListener("pointercancel", finishInteraction);
    refreshButton.addEventListener("click", loadSnapshot);
    resetButton.addEventListener("click", () => setRegion(FULL_FRAME_REGION));

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (saveButton.disabled) return;
      saveButton.disabled = true;
      saveStatus.textContent = "正在保存";
      showError("");
      try {
        const response = await fetch(config.dataset.saveUrl, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail || "save failed");
        }
        setRegion(payload.region);
        saveStatus.textContent =
          payload.camera.stream === "degraded" || payload.camera.last_error
            ? "区域已保存，摄像头尚未恢复"
            : "区域已保存";
      } catch (error) {
        saveStatus.textContent = "";
        showError(
          error.message === "camera_snapshot_required"
            ? "请先获取当前画面"
            : "保存失败，请重试",
        );
      } finally {
        updateSaveAvailability();
      }
    });

    setRegion(region);
    setEditingEnabled(false);
    loadSnapshot();
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", () => {
      const editor = document.querySelector("[data-region-editor]");
      if (editor) initializeEditor(editor);
    });
  }

  return {
    FULL_FRAME_REGION,
    normalizeRegion,
    validateRegion,
    drawRegion,
    moveRegion,
    resizeRegion,
  };
});
