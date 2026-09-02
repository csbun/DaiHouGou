const test = require("node:test");
const assert = require("node:assert/strict");

const {
  FULL_FRAME_REGION,
  normalizeRegion,
  validateRegion,
  drawRegion,
  moveRegion,
  resizeRegion,
} = require("../../src/daihougou/static/region-editor.js");

test("draw clamps to the image and enforces two percent", () => {
  assert.deepEqual(drawRegion({ x: 0.9, y: 0.95 }, { x: 1.2, y: 1.1 }), {
    x: 0.9,
    y: 0.95,
    width: 0.1,
    height: 0.05,
  });
  assert.deepEqual(drawRegion({ x: 0.6, y: 0.7 }, { x: 0.3, y: 0.2 }), {
    x: 0.3,
    y: 0.2,
    width: 0.3,
    height: 0.5,
  });
  assert.deepEqual(drawRegion({ x: 0.5, y: 0.5 }, { x: 0.501, y: 0.501 }), {
    x: 0.5,
    y: 0.5,
    width: 0.02,
    height: 0.02,
  });
  assert.equal(
    validateRegion({ x: 0, y: 0, width: 0.019999, height: 1 }).valid,
    false,
  );
});

test("normalization rounds to six decimals and validates finite bounds", () => {
  assert.deepEqual(
    normalizeRegion({ x: 0.12345649, y: 0, width: 0.5, height: 1 }),
    { x: 0.123456, y: 0, width: 0.5, height: 1 },
  );
  assert.equal(validateRegion(FULL_FRAME_REGION).valid, true);
  assert.equal(validateRegion({ x: Number.NaN, y: 0, width: 1, height: 1 }).valid, false);
  assert.equal(validateRegion({ x: 0, y: 0, width: Infinity, height: 1 }).valid, false);
  assert.equal(validateRegion({ x: 0.5, y: 0, width: 0.6, height: 1 }).valid, false);
});

test("move preserves size at every boundary", () => {
  assert.deepEqual(
    moveRegion({ x: 0.2, y: 0.3, width: 0.4, height: 0.5 }, -1, 1),
    { x: 0, y: 0.5, width: 0.4, height: 0.5 },
  );
});

test("each edge and corner resize stays in bounds and preserves opposite edges", () => {
  const initial = { x: 0.2, y: 0.2, width: 0.5, height: 0.5 };
  assert.deepEqual(resizeRegion(initial, "nw", -0.1, -0.1), {
    x: 0.1,
    y: 0.1,
    width: 0.6,
    height: 0.6,
  });

  const changes = {
    n: [0, -0.1],
    ne: [0.1, -0.1],
    e: [0.1, 0],
    se: [0.1, 0.1],
    s: [0, 0.1],
    sw: [-0.1, 0.1],
    w: [-0.1, 0],
    nw: [-0.1, -0.1],
  };
  for (const [handle, [dx, dy]] of Object.entries(changes)) {
    const resized = resizeRegion(initial, handle, dx, dy);
    assert.equal(validateRegion(resized).valid, true, handle);
    if (handle.includes("w")) assert.equal(resized.x + resized.width, 0.7, handle);
    if (handle.includes("e")) assert.equal(resized.x, 0.2, handle);
    if (handle.includes("n")) assert.equal(resized.y + resized.height, 0.7, handle);
    if (handle.includes("s")) assert.equal(resized.y, 0.2, handle);
  }
});

test("resize enforces minimum size without moving its anchored edge", () => {
  assert.deepEqual(
    resizeRegion({ x: 0.2, y: 0.2, width: 0.5, height: 0.5 }, "nw", 1, 1),
    { x: 0.68, y: 0.68, width: 0.02, height: 0.02 },
  );
  assert.deepEqual(
    resizeRegion({ x: 0.2, y: 0.2, width: 0.5, height: 0.5 }, "se", 1, 1),
    { x: 0.2, y: 0.2, width: 0.8, height: 0.8 },
  );
});
