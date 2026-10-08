"use strict";

(() => {
  const toolbar = document.querySelector(".toolbar");
  const cards = Array.from(document.querySelectorAll(".card"));
  if (!toolbar || cards.length === 0) {
    return;
  }
  const search = document.getElementById("search");
  const count = document.getElementById("count");
  const noMatch = document.getElementById("no-match");
  const filters = { tier: "", target: "" };
  const plugins = (n) => `${n} plugin${n === 1 ? "" : "s"}`;

  function update() {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const card of cards) {
      const visible =
        words.every((word) => card.dataset.search.includes(word)) &&
        (!filters.tier || card.dataset.tier === filters.tier) &&
        (!filters.target || card.dataset.targets.split(" ").includes(filters.target));
      card.hidden = !visible;
      shown += visible ? 1 : 0;
    }
    count.textContent =
      shown === cards.length ? plugins(shown) : `${shown} of ${plugins(cards.length)}`;
    noMatch.hidden = shown > 0;
  }

  const buttons = Array.from(toolbar.querySelectorAll("button[data-filter]"));
  for (const button of buttons) {
    button.addEventListener("click", () => {
      const group = button.dataset.filter;
      filters[group] = button.dataset.value;
      for (const other of buttons) {
        if (other.dataset.filter === group) {
          other.setAttribute("aria-pressed", String(other === button));
        }
      }
      update();
    });
  }
  search.addEventListener("input", update);
  toolbar.hidden = false;
})();
