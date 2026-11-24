"""The admin console's CSS tokens must equal the Flutter app's theme tokens (they are copies), and the
text/background pairs the UI relies on must meet WCAG AA. Parses the real Dart + CSS files."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
DART_TOKENS = ROOT / "frontend" / "lib" / "ui" / "theme" / "pai_tokens.dart"
DART_THEME = ROOT / "frontend" / "lib" / "ui" / "theme" / "pai_theme.dart"
DART_TYPE = ROOT / "frontend" / "lib" / "ui" / "theme" / "pai_typography.dart"
CSS = ROOT / "src" / "pai" / "api" / "admin" / "static" / "css" / "tokens.css"
FONTS = ROOT / "src" / "pai" / "api" / "admin" / "static" / "fonts"
FLUTTER_FONTS = ROOT / "frontend" / "assets" / "fonts"


def kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def dart_colors(text: str) -> dict[str, str]:
    """`static const cyan = Color(0xFF00E5FF);` -> {'cyan': '#00E5FF'} (alpha dropped; only opaque colours)."""
    out = {}
    for name, argb in re.findall(r"static const (\w+)\s*=\s*Color\(0xFF([0-9A-Fa-f]{6})\)", text):
        out[name] = "#" + argb.upper()
    return out


def css_vars() -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+):\s*([^;]+);", CSS.read_text())}


def test_palette_matches_flutter_pai_colors():
    dart, css = dart_colors(DART_TOKENS.read_text()), css_vars()
    expected = {"bg0", "bg1", "bg2", "bg3", "cyan", "cyanDim", "azure", "teal", "amber", "red", "green", "textHigh", "textMid", "textLow", "line"}
    assert expected <= set(dart), f"Dart tokens changed shape: {expected - set(dart)}"
    for name in expected:
        assert css[f"--pai-{kebab(name)}"].upper() == dart[name], f"--pai-{kebab(name)} drifted from PaiColors.{name}"


def test_colour_scheme_extras_match_flutter_dark_scheme():
    theme = DART_THEME.read_text()
    dark = theme[theme.index("static ThemeData dark()"): theme.index("static ThemeData light()")]
    pairs = {"onPrimary": "--pai-on-primary", "primaryContainer": "--pai-primary-container", "secondaryContainer": "--pai-secondary-container",
             "errorContainer": "--pai-error-container", "surfaceContainerHighest": "--pai-surface-highest", "outline": "--pai-outline"}
    css = css_vars()
    for key, var in pairs.items():
        m = re.search(rf"{key}:\s*(?:const\s+)?Color\(0xFF([0-9A-Fa-f]{{6}})\)", dark)
        assert m, f"{key} not found in the Flutter dark scheme"
        assert css[var].upper() == "#" + m.group(1).upper(), f"{var} drifted from ColorScheme.dark({key})"


def test_spacing_and_radii_match_flutter():
    tokens, css = DART_TOKENS.read_text(), css_vars()
    for name in ("xs", "sm", "md", "lg", "xl", "xxl"):
        v = re.search(rf"PaiSpacing.*?static const {name} = ([\d.]+);", tokens, re.S).group(1)
        assert css[f"--pai-space-{name}"] == f"{int(float(v))}px", name
    for name in ("sm", "md", "lg"):
        v = re.search(rf"PaiRadii.*?static const {name} = ([\d.]+);", tokens, re.S).group(1)
        assert css[f"--pai-radius-{name}"] == f"{int(float(v))}px", name


def test_font_families_match_flutter_and_files_are_bundled_with_licences():
    fam = dict(re.findall(r"static const (display|hud|body|mono) = '(\w+)'", DART_TYPE.read_text()))
    css = css_vars()
    assert fam == {"display": "Orbitron", "hud": "Rajdhani", "body": "Inter", "mono": "JetBrainsMono"}
    assert css["--font-display"].startswith('"Orbitron"') and css["--font-hud"].startswith('"Rajdhani"')
    assert css["--font-ui"].startswith('"Inter"') and css["--font-mono"].startswith('"JetBrainsMono"')
    for f in FLUTTER_FONTS.iterdir():                                    # every bundled font + licence is copied verbatim
        copy = FONTS / f.name
        assert copy.exists() and copy.read_bytes() == f.read_bytes(), f.name
    font_css = (CSS.parent / "fonts.css").read_text()
    for f in FONTS.glob("*.ttf"):
        assert f.name in font_css, f"{f.name} has no @font-face"
    assert not re.search(r"url\(\s*['\"]?https?:", font_css)


def _lum(hexc: str) -> float:
    c = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("fg", ["textHigh", "textMid", "textLow", "cyan", "teal", "amber", "red", "green", "azure"])
@pytest.mark.parametrize("bg", ["bg0", "bg1", "bg2", "bg3"])
def test_text_and_status_colours_meet_wcag_aa_on_every_surface(fg, bg):
    c = dart_colors(DART_TOKENS.read_text())
    assert contrast(c[fg], c[bg]) >= 4.5, f"{fg} on {bg} = {contrast(c[fg], c[bg]):.2f}"


def test_text_on_filled_primary_button_meets_aa():
    c, css = dart_colors(DART_TOKENS.read_text()), css_vars()
    assert contrast(css["--pai-on-primary"], c["cyan"]) >= 7


@pytest.mark.parametrize("var", ["--llm", "--tool", "--skipped"])
def test_css_only_accent_colours_meet_aa_on_panels(var):
    c, css = dart_colors(DART_TOKENS.read_text()), css_vars()
    for bg in ("bg1", "bg2", "bg3"):
        assert contrast(css[var], c[bg]) >= 4.5, f"{var} on {bg} = {contrast(css[var], c[bg]):.2f}"
