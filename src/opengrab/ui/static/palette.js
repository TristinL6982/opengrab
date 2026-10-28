// Fuzzy score function for command palette
function ogFuzzyScore(query, text) {
  if (query === "") return 1;
  
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let qi = 0;
  let ti = 0;
  let score = 0;
  let lastMatchedPos = -1;
  
  while (qi < q.length && ti < t.length) {
    if (q[qi] === t[ti]) {
      // Consecutive match gets bonus
      if (lastMatchedPos === ti - 1) {
        score += 2;
      } else {
        score += 1;
      }
      lastMatchedPos = ti;
      qi++;
    }
    ti++;
  }
  
  return qi === q.length ? score : -1;
}

// Rank items for palette
function ogPaletteRank(query, items) {
  const results = items.map(item => {
    const titleScore = ogFuzzyScore(query, item.title);
    const keywordScore = item.keywords ? ogFuzzyScore(query, item.keywords) : -1;
    const maxScore = Math.max(titleScore, keywordScore);
    
    return { ...item, score: maxScore };
  }).filter(item => item.score !== -1);
  
  results.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return 0; // Stable sort
  });
  
  return results.slice(0, 20).map(item => item.title);
}

// Command palette UI logic
if (typeof document !== "undefined") {
  const paletteOverlay = document.getElementById("palette");
  const inputEl = document.getElementById("palette-input");
  const listEl = document.getElementById("palette-list");
  let commands = [];
  let shown = [];

  function allLinks() {
    return (typeof S !== "undefined" && S.links) ? Array.from(S.links.values()) : [];
  }

  async function postEach(links, action) {
    for (const l of links) {
      await api(`/api/links/${l.id}/${action}`, "POST");
    }
  }

  function buildCommands() {
    const cmds = [];
    $$("#tabs .tab[data-tab]").forEach(tab => {
      cmds.push({ title: `Go to ${tab.textContent.trim()}`, run: () => tab.click() });
    });
    const settings = document.getElementById("tab-settings");
    if (settings) {
      settings.querySelectorAll("label").forEach(labelEl => {
        const text = labelEl.textContent.trim().replace(/\s+/g, " ");
        if (!text) return;
        cmds.push({
          title: `Setting: ${text}`,
          run: () => {
            $('.tab[data-tab="settings"]').click();
            const field = labelEl.querySelector("input, select, textarea")
              || (labelEl.htmlFor && document.getElementById(labelEl.htmlFor));
            if (field) field.focus();
          }
        });
      });
    }
    allLinks().forEach(link => {
      cmds.push({
        title: `Download: ${link.name || link.url}`,
        keywords: link.url || "",
        run: () => $('.tab[data-tab="all"]').click()
      });
    });
    cmds.push(
      { title: "Pause all", keywords: "stop halt", run: () =>
          postEach(allLinks().filter(l => l.status === "downloading" || l.status === "waiting"), "pause") },
      { title: "Resume all", keywords: "start continue", run: () =>
          postEach(allLinks().filter(l => l.status === "paused"), "resume") },
      { title: "Clear finished", keywords: "remove completed", run: () =>
          document.getElementById("btn-clear-finished").click() }
    );
    return cmds;
  }

  function runCommand(title) {
    const cmd = commands.find(c => c.title === title);
    closePalette();
    if (!cmd) return;
    try {
      const r = cmd.run();
      if (r && typeof r.catch === "function") r.catch(e => toast(String(e && e.message || e)));
    } catch (e) {
      toast(String(e && e.message || e));
    }
  }

  function render() {
    shown = ogPaletteRank(inputEl.value, commands);
    listEl.replaceChildren();
    shown.forEach((title, i) => {
      const li = document.createElement("li");
      li.textContent = title;
      if (i === 0) li.classList.add("sel");
      li.addEventListener("mousedown", e => e.preventDefault());
      li.addEventListener("click", () => runCommand(title));
      listEl.appendChild(li);
    });
  }

  function moveSel(delta) {
    const items = Array.from(listEl.children);
    if (!items.length) return;
    let i = items.findIndex(li => li.classList.contains("sel"));
    if (i >= 0) items[i].classList.remove("sel");
    i = Math.max(0, Math.min(items.length - 1, (i < 0 ? 0 : i + delta)));
    items[i].classList.add("sel");
    items[i].scrollIntoView({ block: "nearest" });
  }

  function isOpen() { return !paletteOverlay.classList.contains("hidden"); }

  function openPalette() {
    commands = buildCommands();
    inputEl.value = "";
    paletteOverlay.classList.remove("hidden");
    render();
    inputEl.focus();
  }

  function closePalette() { paletteOverlay.classList.add("hidden"); }

  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      if (isOpen()) closePalette(); else openPalette();
      return;
    }
    if (!isOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); moveSel(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveSel(-1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const sel = listEl.querySelector("li.sel");
      if (sel) runCommand(sel.textContent);
    }
  });

  inputEl.addEventListener("input", render);
  paletteOverlay.addEventListener("click", e => { if (e.target === paletteOverlay) closePalette(); });
}

// Export for Node.js usage
if (typeof module !== "undefined" && module.exports) {
  module.exports = { ogFuzzyScore, ogPaletteRank };
}