/* Shared Hub HTML helpers. Loaded by form.html; tested via node --test. */
(function (root) {
  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escAttr(s) {
    return escHtml(s).replace(/'/g, "&#39;");
  }

  function redactSecrets(url) {
    return String(url == null ? "" : url).replace(
      /([?&](?:jackett_)?apikey=)[^&]*/gi,
      "$1***",
    );
  }

  function safeHref(url) {
    const text = redactSecrets(url);
    const lower = text.trim().toLowerCase();
    if (
      lower.startsWith("http:") ||
      lower.startsWith("https:") ||
      lower.startsWith("magnet:")
    ) {
      return text;
    }
    return "";
  }

  root.escHtml = escHtml;
  root.escAttr = escAttr;
  root.redactSecrets = redactSecrets;
  root.safeHref = safeHref;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { escHtml, escAttr, redactSecrets, safeHref };
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
