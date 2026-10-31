# Contributing to OpenGrab

Thanks for helping. Bug reports, site support, translations, documentation and code are all welcome, and you don't need to ask before starting on an open issue.

## Ways to help

- **Report a broken link or site.** The most useful report includes the link (or a similar public one), what you expected, what OpenGrab did, and the file from Settings, Diagnostics, Save diagnostics (it leaves out links, logins and tokens). Remove any private links, cookies or account names first.
- **Add support for a site.** Most hosters need only a JSON template: a URL pattern, a regular expression for the direct link, and markers for "file not found". See `src/opengrab/plugins/templates/example_host.json` and check it with `python -m opengrab --validate-template your-site.json`. Sites that need logic get a Python plugin in `src/opengrab/plugins/builtin/`.
- **Translate.** UI strings live in `src/opengrab/ui/static/i18n/`. Copy `zh-CN.json` to your language code (for example `de.json`) and translate the values. Then add the code to `SUPPORTED` in `i18n.js` and an option to the language lists in `index.html`; that is the only code a new language needs. `python tools/i18n_extract.py --missing <code>` lists what is left.
- **Fix a bug or build a feature.** Issues labelled *good first issue* are small and well described. For anything bigger, open an issue first so we can agree on the approach before you spend time on it.

## Getting set up

```bash
git clone https://github.com/TristinL6982/opengrab.git
cd opengrab
python -m venv .venv
.venv/Scripts/pip install -e . -r requirements-dev.txt     # Linux and macOS: .venv/bin/pip
python -m opengrab --headless                              # web UI on http://127.0.0.1:5678
```

You need Python 3.12. The UI is plain HTML, CSS and JavaScript in `src/opengrab/ui/static/` with no build step: edit and reload.

## Before you open a pull request

1. **Run the tests.** All of them must pass on your machine:
   ```bash
   python -m pytest -q -p no:cacheprovider
   ```
2. **Add a test for your change.** A bug fix comes with a test that fails without the fix. Tests use local fixture servers in `tests/helpers/`, never the real internet.
3. **Never delete, skip or loosen an existing test** to make your change pass. If a test is wrong, explain why in the pull request.
4. **Check JavaScript syntax** for any `.js` file you touched: `node --check path/to/file.js`.
5. **New UI text needs a Chinese translation** (or an entry in the `keep` list for product names), otherwise the translation test fails. Ask in the pull request if you can't translate it.
6. **Keep secrets out.** No API keys, tokens, cookies or personal paths in code, tests, logs or screenshots.

CI runs the same tests on Windows, Linux and macOS, builds the Windows app and installer, and builds the Docker image. A pull request is merged when CI is green and a maintainer has reviewed it.

## Rules for site support

- Write support from how the site behaves in public. Do not copy code from other download managers, including JDownloader plugins, whose licences differ from ours.
- Do not add anything whose purpose is to get around paywalls, DRM or copy protection. Captchas and bot checks are handled by asking the user, by their own accounts, or by solver services they choose.

## Style

- Python: standard library first, type hints on public functions, small focused modules. New features live in `src/opengrab/features/` and register their routes there.
- Commit messages: a short summary line (`fix: …`, `feat: …`, `docs: …`, `test: …`), then a body that says why.
- Write UI text in plain, active language, in sentence case.

## Be kind

Assume good intent, keep criticism about the code, and help newcomers. Harassment or personal attacks in issues, pull requests or discussions lead to removal from the project.

## Licence

By contributing you agree that your work is released under the project's [MIT License](../LICENSE).
