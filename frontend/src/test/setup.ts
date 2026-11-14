import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

/**
 * jsdom does not implement `PointerEvent`, and dnd-kit (plus the board's
 * pointer-driven resize/pan handlers) depend on it. This polyfill maps it onto
 * `MouseEvent`, preserving `clientX`/`clientY` and adding the pointer-capture
 * no-ops the implementation calls. It is limited to the test environment and
 * never changes production behaviour.
 */
if (typeof window !== "undefined" && !("PointerEvent" in window)) {
  class PointerEventPolyfill extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    readonly isPrimary: boolean;
    readonly width: number;
    readonly height: number;
    readonly pressure: number;

    constructor(type: string, params: PointerEventInit = {}) {
      super(type, params);
      this.pointerId = params.pointerId ?? 1;
      this.pointerType = params.pointerType ?? "mouse";
      this.isPrimary = params.isPrimary ?? true;
      this.width = params.width ?? 1;
      this.height = params.height ?? 1;
      this.pressure = params.pressure ?? 0;
    }
  }

  // @ts-expect-error assigning a polyfill onto the jsdom window
  window.PointerEvent = PointerEventPolyfill;
}

// jsdom's Element lacks pointer capture; the board calls these defensively.
if (typeof Element !== "undefined") {
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = function setPointerCapture() {
      /* no-op in jsdom */
    };
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = function releasePointerCapture() {
      /* no-op in jsdom */
    };
  }
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = function hasPointerCapture() {
      return false;
    };
  }
}

afterEach(() => {
  cleanup();
});
