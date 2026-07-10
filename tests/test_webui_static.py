import re
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "rootfs" / "app" / "webui_static" / "index.html"


def _scripts():
    html = INDEX_HTML.read_text(encoding="utf-8")
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def _run_node(js: str) -> str:
    proc = subprocess.run(
        ["node", "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


class WebuiStaticRegressionTests(unittest.TestCase):
    def test_initial_auto_theme_uses_system_preference_before_alpine_boots(self):
        initial_script = _scripts()[0]
        js = textwrap.dedent(
            f"""
            const vm = require('vm');
            let dataTheme = '';
            class FixedDate extends Date {{
              constructor(...args) {{ super(...(args.length ? args : ['2026-06-26T12:00:00+08:00'])); }}
              static now() {{ return new Date('2026-06-26T12:00:00+08:00').getTime(); }}
            }}
            const context = {{
              Date: FixedDate,
              localStorage: {{ getItem: (key) => key === 'ipa-theme-mode' ? 'auto' : null }},
              document: {{ documentElement: {{ setAttribute: (_name, value) => {{ dataTheme = value; }} }} }},
              window: {{ matchMedia: (query) => ({{ matches: query.includes('dark') }}) }},
            }};
            vm.createContext(context);
            vm.runInContext({initial_script!r}, context);
            if (dataTheme !== 'dark') {{
              throw new Error(`expected initial auto theme to follow system dark, got ${{dataTheme}}`);
            }}
            console.log(dataTheme);
            """
        )
        self.assertEqual(_run_node(js), "dark")

    def test_resolve_theme_auto_uses_system_preference(self):
        app_script = _scripts()[-1]
        js = textwrap.dedent(
            f"""
            const vm = require('vm');
            class FixedDate extends Date {{
              constructor(...args) {{ super(...(args.length ? args : ['2026-06-26T12:00:00+08:00'])); }}
              static now() {{ return new Date('2026-06-26T12:00:00+08:00').getTime(); }}
            }}
            const context = {{
              Date: FixedDate,
              window: {{ matchMedia: (query) => ({{ matches: query.includes('dark') }}) }},
              document: {{ documentElement: {{ setAttribute: () => {{}} }} }},
              localStorage: {{ setItem: () => {{}}, getItem: () => null }},
              navigator: {{}},
              setTimeout: () => 0,
              setInterval: () => 0,
              console,
            }};
            vm.createContext(context);
            vm.runInContext({app_script!r}, context);
            const theme = context.resolveTheme('auto');
            if (theme !== 'dark') {{
              throw new Error(`expected auto theme to follow system dark, got ${{theme}}`);
            }}
            console.log(theme);
            """
        )
        self.assertEqual(_run_node(js), "dark")

    def test_repo_url_input_is_addressable_by_copy_fallback(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        subscription_input = re.search(
            r'<input\s+[^>]*:value="repoInfo\.url"[^>]*readonly[^>]*>',
            html,
        )
        self.assertIsNotNone(subscription_input, "subscription URL input not found")
        assert subscription_input is not None
        self.assertIn('x-ref="repoUrlInput"', subscription_input.group(0))
        self.assertIn("this.$refs && this.$refs.repoUrlInput", html)
        self.assertIn("input.select();", html)


if __name__ == "__main__":
    unittest.main()
