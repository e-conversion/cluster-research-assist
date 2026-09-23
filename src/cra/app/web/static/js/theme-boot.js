// Runs before first paint, so the page never flashes the wrong theme.
// Loaded as a file rather than inline so the Content-Security-Policy can
// refuse inline scripts altogether.
  // before first paint, so the page never flashes the wrong theme
  try {
    const mode = localStorage.getItem("econverse-theme");
    if (mode === "light" || mode === "dark") document.documentElement.dataset.theme = mode;
  } catch (e) { /* private mode */ }
