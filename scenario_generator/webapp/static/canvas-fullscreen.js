// Expanded canvas presentation that keeps the application header accessible.

const canvasFullscreenButton = $("#canvas-fullscreen");
const canvasBackgroundRegions = [
  ".actors",
  ".inspector",
  "#speed-profile",
  "#tables",
];

/** Redraw after both the expanded layout and the canvas dimensions have settled. */
function redrawExpandedCanvas() {
  // The fixed presentation can settle one frame after its class changes.
  requestAnimationFrame(() => requestAnimationFrame(draw));
}

/** Publish the current header height so the layout can size around it. */
function syncHeaderHeight() {
  const headerHeight = document.querySelector("header").getBoundingClientRect().height;
  document.documentElement.style.setProperty("--header-height", `${headerHeight}px`);
}

/** Keep expanded-mode styling, accessibility text, and icon state in sync. */
function setCanvasExpanded(expanded, restoreFocus = true) {
  syncHeaderHeight();
  document.body.classList.toggle("canvas-expanded", expanded);
  canvasBackgroundRegions.forEach((selector) => {
    const element = document.querySelector(selector);
    if (element) element.inert = expanded;
  });
  const label = expanded ? "Exit fullscreen" : "Enter fullscreen";
  canvasFullscreenButton.setAttribute("aria-label", label);
  canvasFullscreenButton.title = label;
  canvasFullscreenButton
    .querySelector('[data-fullscreen-icon="enter"]')
    .toggleAttribute("hidden", expanded);
  canvasFullscreenButton
    .querySelector('[data-fullscreen-icon="exit"]')
    .toggleAttribute("hidden", !expanded);
  redrawExpandedCanvas();
  // Focus only follows a toggle the user made from the button; an Escape exit
  // leaves focus on whatever the user was working with inside the canvas.
  if (restoreFocus) requestAnimationFrame(() => canvasFullscreenButton.focus());
}

// Both button and Escape key use the same state transition to avoid icon drift.
canvasFullscreenButton.onclick = () => {
  setCanvasExpanded(!document.body.classList.contains("canvas-expanded"));
};

document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape"
    && document.body.classList.contains("canvas-expanded")
  ) {
    setCanvasExpanded(false, false);
  }
});

syncHeaderHeight();
window.addEventListener("resize", syncHeaderHeight);
