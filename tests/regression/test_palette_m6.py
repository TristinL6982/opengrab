import subprocess
import sys
import json
import tempfile
import os

def test_node_check():
    """Test that palette.js passes JS check."""
    result = subprocess.run([
        "node", "--check", "src/opengrab/ui/static/palette.js"
    ], cwd="C:/Users/trist/opengrab", capture_output=True, text=True)
    assert result.returncode == 0

def test_fuzzy_functions():
    """Test fuzzy score and ranking functions."""
    script = f"""
const path = require('path');
const palettePath = path.resolve('{os.path.abspath("C:/Users/trist/opengrab/src/opengrab/ui/static/palette.js")}');
delete require.cache[require.resolve('{os.path.abspath("C:/Users/trist/opengrab/src/opengrab/ui/static/palette.js")}')]
const palette = require('{os.path.abspath("C:/Users/trist/openggrab/src/opengrab/ui/static/palette.js")}');
console.log(JSON.stringify([
  palette.ogFuzzyScore("pa all","Pause all") > 0,
  palette.ogFuzzyScore("xyz","Pause all") == -1,
  palette.ogFuzzyScore("","anything") == 1,
  palette.ogFuzzyScore("set","Settings") > palette.ogFuzzyScore("set","Reset all"),
  JSON.stringify(palette.ogPaletteRank("pause", [
    {{title:"Go to Queue"}},
    {{title:"Pause all"}},
    {{title:"Resume all"}}
  ])) === JSON.stringify(["Pause all"]),
  palette.ogPaletteRank("speed", [
    {{title:"Setting: Limit", keywords:"speed bandwidth"}}
  ]).length === 1,
  palette.ogPaletteRank("", [
    {{title:"A"}}, {{title:"B"}}, {{title:"C"}}
  ]).length === 3,
  palette.ogPaletteRank("", Array(25).fill({{title:"Item"}})).length === 20
]));
"""
    result = subprocess.run([
        "node", "-e", script
    ], cwd="C:/Users/trist/opengrab", capture_output=True, text=True)
    
    output = json.loads(result.stdout.strip())
    assert output[0] == True, "ogFuzzyScore('pa all', 'Pause all') > 0"
    assert output[1] == True, "ogFuzzyScore('xyz', 'Pause all') == -1"
    assert output[2] == True, "ogFuzzyScore('', 'anything') == 1"
    assert output[3] == True, "prefix beats non-prefix in fuzzy matching"
    assert output[4] == True, "ogPaletteRank correctly filters and sorts by score"
    assert output[5] == True, "keywords match works"
    assert output[6] == True, "empty query preserves original order"
    assert output[7] == True, "limit to 20 results for large input"

def test_index_html():
    """Test that index.html contains the palette overlay and script."""
    with open("C:/Users/trist/opengrab/src/opengrab/ui/static/index.html", "r") as f:
        content = f.read()
    
    assert 'id="palette"' in content
    assert 'id="palette-input"' in content
    assert 'id="palette-list"' in content
    
    # Check that palette.js is included after app.js
    app_idx = content.find('<script src="app.js"></script>')
    palette_idx = content.find('<script src="palette.js"></script>')
    assert app_idx != -1, "app.js should be present"
    assert palette_idx != -1, "palette.js should be present after app.js"
    assert palette_idx > app_idx, "palette.js script tag must come after app.js"

def test_style_css():
    """Test that style.css has required palette styles."""
    with open("C:/Users/trist/opengrab/src/opengrab/ui/static/style.css", "r") as f:
        content = f.read()
    
    assert '#palette' in content
    assert '#palette-list li' in content
    assert 'li.sel' in content

if __name__ == "__main__":
    test_node_check()
    test_fuzzy_functions()
    test_index_html()
    test_style_css()
    print("All tests passed.")