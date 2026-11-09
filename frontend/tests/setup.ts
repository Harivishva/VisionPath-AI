import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom has no canvas implementation; provide a no-op 2D context so overlay drawing is exercised safely.
HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
  clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(), stroke: vi.fn(),
  drawImage: vi.fn(), save: vi.fn(), restore: vi.fn(), setLineDash: vi.fn(), fill: vi.fn(), arc: vi.fn(), fillText: vi.fn(),
})) as unknown as typeof HTMLCanvasElement.prototype.getContext;

afterEach(() => cleanup());
