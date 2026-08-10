/* Compatibility filter for Chrome issue codes not yet handled by DevTools MCP. */
const originalWarn = console.warn.bind(console);

console.warn = (...values) => {
  const message = values.map((value) => String(value)).join(" ");
  if (message.startsWith("No handler registered for issue code PerformanceIssue")) {
    return;
  }
  originalWarn(...values);
};
