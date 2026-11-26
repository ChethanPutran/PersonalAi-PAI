"""End-to-end checks of the real plugin library against a real accessibility-enabled app, run by
e2e_session.sh inside a private desktop. Prints "E2E OK" when every check passes."""
import base64
import json
import os
import struct
import sys
import zlib
from pathlib import Path

REGISTRY_ROOT = Path(os.environ["PAI_REGISTRY_ROOT"])
sys.path.insert(0, str(REGISTRY_ROOT / "shared" / "testing"))
from portal_harness import PluginSession  # noqa: E402

SO = Path(os.environ["PAI_CC_SO"])
plugin = PluginSession(SO, dict(os.environ, PAI_CC_INPUT_CONSENT="1", PAI_CC_SCREEN_CONSENT="1", PAI_CC_INPUT_BACKEND="xtest"),
                       timeout=60)


def call(cap, **params):
    return plugin.call(cap, params)


def flat(node, out=None):
    out = [] if out is None else out
    out.append(node)
    for c in node["children"]:
        flat(c, out)
    return out


def observe(**params):
    r = call("computer.observe", **params)
    assert r["error"] is None, r
    res = r["result"]
    return res, flat(json.loads(res["tree"])[0]) if res.get("tree") not in (None, "null") else []


def find(nodes, role, text=None):
    return next(n for n in nodes if n["role"] == role and (text is None or text in n["name"] or text in n["text"]))


def check(cond, what):
    if not cond:
        print("FAILED:", what)
        sys.exit(1)


# ---- status
st = call("computer.status")["result"]
check(st["ready"] == "true" and st["missing"] == "[]" and st["session"] == "x11", f"status ready: {st}")
check(json.loads(st["features"]) == {"tree": True, "screenshot": True, "ocr": False, "input": True}, "features")

# ---- observe: the active window's tree, with the contract's shape
res, nodes = observe()
check(json.loads(res["app"])["title"] == "PAI Test App", f"app: {res['app']}")
check(json.loads(res["screen"]) == {"width": 1280, "height": 800}, f"screen: {res['screen']}")
check(res["observation_id"].startswith("obs-") and res["truncated"] == "false", "observation id / truncated")
roles = sorted({n["role"] for n in nodes})
check(roles == ["button", "checkbox", "input", "text", "window"], f"roles: {roles}")
for n in nodes:
    check({"id", "role", "name", "text", "bounds", "enabled", "focused", "checked", "clickable", "scrollable", "editable",
           "children"} <= set(n), f"node keys: {n}")
button, box, entry = find(nodes, "button", "hello"), find(nodes, "checkbox"), find(nodes, "input")
check(button["clickable"] and button["enabled"] and box["checked"] is False and entry["editable"], "states")
check(find(nodes, "text", "idle")["text"] == "Status: idle", "label text")

# ---- budget: a tiny budget truncates and says so
res_small, small = observe(max_nodes="2")
check(len(small) == 2 and res_small["truncated"] == "true" and res_small["node_count"] == "2", "max_nodes truncation")
res, nodes = observe()                                       # a fresh, full observation
button, box, entry = find(nodes, "button", "hello"), find(nodes, "checkbox"), find(nodes, "input")

# ---- ids are only valid within their observation
old = res["observation_id"]
res2, nodes2 = observe()
r = call("computer.click", element_id=button["id"], observation_id=old)
check(r["error"] == "stale_observation" and res2["observation_id"] in r["result"]["hint"], f"stale: {r}")
check(call("computer.click", element_id="e99")["error"] == "unknown_element", "unknown element")
button, box, entry = find(nodes2, "button", "hello"), find(nodes2, "checkbox"), find(nodes2, "input")

# ---- semantic click, verified by observing again
r = call("computer.click", element_id=button["id"])
check(r["error"] is None and r["result"]["method"] == "accessibility_action", f"semantic click: {r}")
_, nodes = observe()
check(find(nodes, "text", "Status")["text"] == "Status: clicked", "the label changed after the click")

# ---- checkbox: semantic toggle, then a POINTER click by coordinates (XTEST) toggles it back
box = find(nodes, "checkbox")
check(call("computer.click", element_id=box["id"])["error"] is None, "toggle on")
_, nodes = observe()
box = find(nodes, "checkbox")
check(box["checked"] is True, "checked after the semantic click")
x, y = box["bounds"][0] + box["bounds"][2] // 2, box["bounds"][1] + box["bounds"][3] // 2
r = call("computer.click", x=x, y=y)
check(r["error"] is None and r["result"]["method"] == "pointer", f"pointer click: {r}")
_, nodes = observe()
check(find(nodes, "checkbox")["checked"] is False, "unchecked after the pointer click")

# ---- typing: straight into a text field (any character), clear_first replaces
entry = find(nodes, "input")
r = call("computer.type", text="héllo ✓ wörld", element_id=entry["id"])
check(r["error"] is None and r["result"]["method"] == "accessibility_text" and r["result"]["typed"] == "13", f"type: {r}")
_, nodes = observe()
check(find(nodes, "input")["text"] == "héllo ✓ wörld", f"entry text: {find(nodes, 'input')['text']!r}")
call("computer.type", text="second", element_id=find(nodes, "input")["id"], clear_first="true")
_, nodes = observe()
check(find(nodes, "input")["text"] == "second", "clear_first replaced the content")

# ---- keyboard events through XTEST reach the app (focus the field, then type without an element)
entry = find(nodes, "input")
call("computer.click", x=entry["bounds"][0] + 20, y=entry["bounds"][1] + 10)
check(call("computer.key", keys="ctrl+a")["error"] is None, "ctrl+a")
r = call("computer.type", text="Typed!")
check(r["error"] is None and r["result"]["method"] == "keyboard", f"keyboard type: {r}")
_, nodes = observe()
check(find(nodes, "input")["text"] == "Typed!", f"keyboard typing reached the app: {find(nodes, 'input')['text']!r}")

# ---- scroll, drag, shortcuts, foreground app, apps
check(call("computer.scroll", direction="down", amount="2", x="300", y="300")["error"] is None, "scroll")
r = call("computer.swipe", **{"from": json.dumps({"x": 100, "y": 100}), "to": json.dumps({"x": 220, "y": 160})})
check(r["error"] is None, f"swipe: {r}")
check(call("computer.long_press", x="300", y="300", duration_ms="150")["error"] is None, "long_press")
fg = call("computer.apps.foreground")["result"]
check(fg["title"] == "PAI Test App", f"foreground: {fg}")
check(call("computer.apps.launch", name="no-such-app-zzz")["error"] == "app_not_found", "launch unknown")

# ---- screenshot straight from the X server: a real PNG of the real screen
res, _ = observe(include_screenshot="true")
shot = json.loads(res["screenshot"])
png = base64.b64decode(shot["base64"])
check(png[:8] == b"\x89PNG\r\n\x1a\n" and (shot["width"], shot["height"]) == (1280, 800) and shot["mime"] == "image/png", "png header")
i, idat = 8, b""
while i < len(png):
    n = struct.unpack(">I", png[i:i + 4])[0]
    if png[i + 4:i + 8] == b"IDAT":
        idat += png[i + 8:i + 8 + n]
    i += 12 + n
raw = zlib.decompress(idat)
stride = 1 + shot["width"] * 3
pixel = lambda px, py: tuple(raw[py * stride + 1 + px * 3: py * stride + 4 + px * 3])  # noqa: E731
check(pixel(1275, 795) == (0, 0, 0) and pixel(5, 5) != (0, 0, 0), "the screenshot shows the app on the screen")
res_only, _ = observe(include_tree="false", include_screenshot="true")
check(res_only["tree"] == "null" and json.loads(res_only["screenshot"])["width"] == 1280, "screenshot without the tree")

plugin.close()
print("E2E OK")
