/* Dashboard entry point. Loaded last so every pages/*.js has registered. */

(function start() {
  applyTheme(storedTheme());
  wireChrome();

  const initial = window.location.hash.slice(1);
  if (initial && PAGES.includes(initial)) {
    state.page = initial;
  }

  // First paint must not depend on the network. This used to be chained after
  // refresh(), so an agent that accepted the connection and never answered
  // left the content area empty forever — no nav highlight, no banner, no
  // timeout. navigate() now runs first: it applies the nav/section classes and
  // renders the "still contacting the agent…" placeholder (render() knows the
  // first load has not settled), and refresh() fills the real data in behind
  // it. api()'s AbortController guarantees that eventually happens either way.
  navigate(state.page);

  loadLocalProviderRegistry()
    .then(refresh)
    .then(() => {
      // Re-run once the payload is in: the pollers for the landing page were
      // started above, but the page itself was the placeholder until now.
      navigate(state.page);
      startUpdatePolling();
    })
    .catch((e) => {
      setBanner(`Agent unreachable: ${e.message}`, "error");
      state.firstLoadComplete = true;
      updateStatusBadge();
      navigate("overview");
    });
})();
