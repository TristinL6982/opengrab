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
      if (lastMatchedPos === ti - 1) {
        // Consecutive match gets bonus
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
  const paletteOverlay = document.getElementById('palette');
  const inputEl = document.getElementById('palette-input');
  const listEl = document.getElementById('palette-list');
  
  function buildCommands() {
    const commands = [];
    
    // Tab navigation commands
    const tabs = $$('[data-tab]');
    tabs.forEach(tab => {
      const label = tab.textContent.trim();
      commands.push({
        title: `Go to ${label}`,
        run: () => { tab.click(); }
      });
    });
    
    // Settings commands
    const settingsTab = document.getElementById('tab-settings');
    if (settingsTab) {
      const settingLabels = settingsTab.querySelectorAll('label');
      settingLabels.forEach(labelEl => {
        const text = labelEl.textContent.trim();
        commands.push({
          title: `Setting: ${text}`,
          run: () => {
            // Click settings tab
            document.querySelector('[data-tab="settings"]').click();
            // Focus the corresponding input or select element
            const input = labelEl.nextElementSibling;
            if (input && (input.tagName === 'INPUT' || input.tagName === 'SELECT')) {
              input.focus();
            }
          }
        });
      });
    }
    
    // Download commands (links)
    if (S && S.links) {
      S.links.forEach(link => {
        const name = link.name || link.url;
        commands.push({
          title: `Download: ${name}`,
          run: () => {
            document.querySelector('[data-tab="queue"]').click();
          }
        });
      });
    }
    
    // Actions
    commands.push(
      { title: "Pause all", run: () => fetch('/api/links/pause', { method: 'POST' }).catch(e => toast(e.message)) },
      { title: "Resume all", run: () => fetch('/api/links/resume', { method: 'POST' }).catch(e => toast(e.message)) },
      { title: "Clear finished", run: () => document.getElementById('btn-clear-finished').click() }
    );
    
    return commands;
  }
  
  function updatePalette(query) {
    const items = buildCommands();
    const rankedItems = ogPaletteRank(query, items);
    
    listEl.innerHTML = '';
    rankedItems.forEach(title => {
      const li = document.createElement('li');
      li.textContent = title;
      li.addEventListener('click', () => {
        try {
          const cmd = items.find(i => i.title === title);
          if (cmd) cmd.run();
        } catch (e) {
          toast(e.message);
        }
        paletteOverlay.classList.add('hidden');
      });
      listEl.appendChild(li);
    });
    
    if (listEl.children.length > 0) {
      listEl.children[0].classList.add('sel');
    }
  }
  
  let currentCommands = [];
  
  function openPalette() {
    paletteOverlay.classList.remove('hidden');
    inputEl.value = '';
    updatePalette('');
    inputEl.focus();
  }
  
  function closePalette() {
    paletteOverlay.classList.add('hidden');
  }
  
  function handleKeyDown(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      openPalette();
      return;
    }
    
    if (paletteOverlay.classList.contains('hidden')) return;
    
    switch (e.key) {
      case 'Escape':
        closePalette();
        break;
      case 'ArrowUp':
        e.preventDefault();
        const currentSel = listEl.querySelector('li.sel');
        if (currentSel) {
          const prev = currentSel.previousElementSibling;
          if (prev) {
            currentSel.classList.remove('sel');
            prev.classList.add('sel');
          }
        }
        break;
      case 'ArrowDown':
        e.preventDefault();
        const currentSel2 = listEl.querySelector('li.sel');
        if (currentSel2) {
          const next = currentSel2.nextElementSibling;
          if (next) {
            currentSel2.classList.remove('sel');
            next.classList.add('sel');
          }
        } else {
          const first = listEl.firstElementChild;
          if (first) first.classList.add('sel');
        }
        break;
      case 'Enter':
        e.preventDefault();
        const sel = listEl.querySelector('li.sel');
        if (sel) {
          try {
            const cmd = currentCommands.find(c => c.title === sel.textContent);
            if (cmd) cmd.run();
          } catch (e) {
            toast(e.message);
          }
          closePalette();
        }
        break;
    }
  }
  
  inputEl.addEventListener('input', () => {
    updatePalette(inputEl.value);
  });
  
  document.addEventListener('keydown', handleKeyDown);
  
  // Ensure updatePalette is called correctly
  function handlePaletteInput() {
    const query = inputEl.value;
    const items = buildCommands();
    currentCommands = items; // Store for reference
    updatePalette(query);
  }
  
  inputEl.addEventListener('input', handlePaletteInput);
}

// Export for Node.js usage
if (typeof module !== "undefined" && module.exports) {
  module.exports = { ogFuzzyScore, ogPaletteRank };
}