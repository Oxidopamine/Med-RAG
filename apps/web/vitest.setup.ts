import "@testing-library/jest-dom/vitest";

Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
  configurable: true,
  value: () => false,
});

Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
  configurable: true,
  value: () => undefined,
});

Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
  configurable: true,
  value: () => undefined,
});

// jsdom implements neither half of the object-URL API. Components that hold a blob for
// the life of a view - the source page viewer does - allocate one and release it on
// unmount, and without these the release throws during cleanup.
Object.defineProperty(URL, "createObjectURL", {
  configurable: true,
  value: () => "blob:test",
});

Object.defineProperty(URL, "revokeObjectURL", {
  configurable: true,
  value: () => undefined,
});
