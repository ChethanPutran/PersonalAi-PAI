// Unit tests for the pure logic of computer_control (no AT-SPI, X11 or portal). Built and run by
// tests/test_computer_control_1_0_0.py:  cmake -DPAI_CC_BUILD_TESTS=ON ... && ./cc_unit_test
#include "cc_model.h"
#include "pai_desktop_apps.h"
#include "pai_input_keys.h"

#include <cstdio>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

using namespace pai::cc;

static int failures = 0;
#define CHECK(cond)                                                                                  \
  do {                                                                                               \
    if (!(cond)) { std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); ++failures; } \
  } while (0)
#define CHECK_EQ(a, b)                                                                               \
  do {                                                                                               \
    auto _a = (a); auto _b = (b);                                                                    \
    if (!(_a == _b)) { std::fprintf(stderr, "FAIL %s:%d  %s == %s\n", __FILE__, __LINE__, #a, #b); ++failures; } \
  } while (0)

// ---- a fake UI: handle -> info, children
struct FakeUi : NodeSource {
    std::map<Handle, NodeInfo> info;
    std::map<Handle, std::vector<Handle>> kids;
    int info_calls = 0;
    bool Info(Handle h, NodeInfo& out) override {
        ++info_calls;
        auto it = info.find(h);
        if (it == info.end()) return false;
        out = it->second;
        return true;
    }
    std::vector<Handle> Children(Handle h) override { return kids[h]; }
    Handle Add(Handle id, const std::string& role, const std::string& name, Handle parent = 0) {
        NodeInfo n;
        n.role = role;
        n.name = name;
        n.bounds = {10, 20, 100, 30};
        info[id] = n;
        if (parent) kids[parent].push_back(id);
        return id;
    }
};

static void TestRoles() {
    CHECK_EQ(NormalizeRole("push button", false), "button");
    CHECK_EQ(NormalizeRole("Push_Button", false), "button");          // case and separators do not matter
    CHECK_EQ(NormalizeRole("check box", false), "checkbox");
    CHECK_EQ(NormalizeRole("toggle button", false), "switch");
    CHECK_EQ(NormalizeRole("radio button", false), "radio");
    CHECK_EQ(NormalizeRole("menu item", false), "menu_item");
    CHECK_EQ(NormalizeRole("check menu item", false), "menu_item");
    CHECK_EQ(NormalizeRole("page tab", false), "tab");
    CHECK_EQ(NormalizeRole("list item", false), "list_item");
    CHECK_EQ(NormalizeRole("frame", false), "window");
    CHECK_EQ(NormalizeRole("alert", false), "dialog");
    CHECK_EQ(NormalizeRole("scroll pane", false), "scroll");
    CHECK_EQ(NormalizeRole("icon", false), "image");
    CHECK_EQ(NormalizeRole("link", false), "link");
    CHECK_EQ(NormalizeRole("label", false), "text");
    CHECK_EQ(NormalizeRole("entry", false), "input");
    CHECK_EQ(NormalizeRole("password text", false), "input");
    CHECK_EQ(NormalizeRole("text", false), "text");                  // read-only text is plain text...
    CHECK_EQ(NormalizeRole("text", true), "input");                  // ...an editable one is an input
    CHECK_EQ(NormalizeRole("combo box", false), "button");
    CHECK_EQ(NormalizeRole("combo box", true), "input");
    CHECK_EQ(NormalizeRole("panel", false), "other");
    CHECK_EQ(NormalizeRole("something new", false), "other");        // unknown roles never break an observation
    CHECK_EQ(NormalizeRole("", false), "other");
    const std::set<std::string> allowed = {"button", "link", "text", "input", "checkbox", "switch", "radio", "menu", "menu_item",
                                           "tab", "list", "list_item", "image", "window", "dialog", "scroll", "other"};
    for (const char* r : {"push button", "label", "entry", "check box", "toggle button", "radio button", "menu", "menu item",
                          "page tab", "list", "list item", "image", "frame", "dialog", "scroll pane", "panel", "slider"})
        CHECK(allowed.count(NormalizeRole(r, false)) == 1);
}

static void TestTreeStructureAndFlattening() {
    FakeUi ui;
    ui.Add(1, "frame", "Calculator");
    ui.Add(2, "panel", "", 1);                         // structural: no name, not interactive -> spliced out
    ui.Add(3, "push button", "7", 2);
    ui.Add(4, "push button", "8", 2);
    ui.Add(5, "filler", "", 2);                        // another empty container, with a child
    ui.Add(6, "label", "Result", 5);
    ui.info[4].enabled = false;
    ui.info[3].clickable = true;
    ui.info[6].text = "42";
    Tree t = BuildTree(ui, 1, {});
    CHECK_EQ(t.roots.size(), 1u);
    CHECK_EQ(t.count, 4);
    CHECK(!t.truncated);
    const Node& win = t.roots[0];
    CHECK_EQ(win.id, "e1");
    CHECK_EQ(win.role, "window");
    CHECK_EQ(win.children.size(), 3u);                 // 7, 8 and Result: the panels are gone
    CHECK_EQ(win.children[0].name, "7");
    CHECK(win.children[0].clickable);
    CHECK(!win.children[1].enabled);
    CHECK_EQ(win.children[2].role, "text");
    CHECK_EQ(win.children[2].text, "42");
    CHECK_EQ(t.handles.at("e2"), (Handle)3);           // ids map back to the platform handles
    CHECK_EQ(t.handles.size(), 4u);
}

static void TestHiddenElementsAreSkippedWithTheirSubtree() {
    FakeUi ui;
    ui.Add(1, "frame", "W");
    ui.Add(2, "push button", "Visible", 1);
    ui.Add(3, "panel", "Hidden group", 1);
    ui.Add(4, "push button", "Inside hidden", 3);
    ui.info[3].showing = false;
    Tree t = BuildTree(ui, 1, {});
    CHECK_EQ(t.count, 2);
    CHECK_EQ(t.roots[0].children.size(), 1u);
    CHECK_EQ(t.roots[0].children[0].name, "Visible");
}

static void TestNodeBudgetKeepsTheTopLevelControls() {
    FakeUi ui;
    ui.Add(1, "frame", "W");
    for (int i = 0; i < 5; ++i) {                      // 5 groups, each with a deep chain below
        Handle g = 10 + i * 100;
        ui.Add(g, "menu", "group" + std::to_string(i), 1);
        for (int d = 1; d < 5; ++d) ui.Add(g + d, "push button", "deep" + std::to_string(d), g + d - 1);
    }
    Limits l;
    l.max_nodes = 8;
    Tree t = BuildTree(ui, 1, l);
    CHECK_EQ(t.count, 8);
    CHECK(t.truncated);
    CHECK_EQ(t.roots[0].children.size(), 5u);          // breadth first: all five top-level groups are in, the depth is what is cut
}

static void TestDepthLimit() {
    FakeUi ui;
    ui.Add(1, "frame", "W");
    ui.Add(2, "menu", "A", 1);
    ui.Add(3, "menu", "B", 2);
    ui.Add(4, "menu", "C", 3);
    Limits l;
    l.max_depth = 2;                                   // the window and its direct children only
    Tree t = BuildTree(ui, 1, l);
    CHECK_EQ(t.count, 2);
    CHECK(t.truncated);
    l.max_depth = 4;
    Tree full = BuildTree(ui, 1, l);
    CHECK_EQ(full.count, 4);
    CHECK(!full.truncated);
    l.max_depth = 1;
    CHECK_EQ(BuildTree(ui, 1, l).count, 1);
}

static void TestPathologicalTreesAreBounded() {
    FakeUi ui;
    ui.Add(1, "frame", "W");
    Handle prev = 1;
    for (Handle i = 2; i < 6000; ++i) { ui.Add(i, "filler", "", prev); prev = i; }      // 6000 nested empty containers
    Limits l;
    l.max_nodes = 5;
    Tree t = BuildTree(ui, 1, l);
    CHECK(t.truncated);
    CHECK(ui.info_calls < 400);                        // gave up after a bounded number of visits
    FakeUi missing;                                    // a root that cannot be read
    CHECK_EQ(BuildTree(missing, 99, {}).count, 0);
}

static void TestTextIsBoundedAndSafe() {
    FakeUi ui;
    ui.Add(1, "frame", std::string(1000, 'a'));
    ui.info[1].text = std::string(1000, 'b');
    Tree t = BuildTree(ui, 1, {});
    CHECK(t.roots[0].name.size() < 210);
    CHECK(t.roots[0].text.size() < 310);
    CHECK_EQ(TruncateUtf8("héllo wörld", 5), std::string("héllo\xE2\x80\xA6"));
    CHECK_EQ(TruncateUtf8("short", 50), "short");
    CHECK_EQ(SanitizeUtf8("a\xFF" "b"), std::string("a\xEF\xBF\xBD" "b"));     // invalid byte -> U+FFFD
    CHECK_EQ(SanitizeUtf8("\xC0\xAF"), std::string("\xEF\xBF\xBD\xEF\xBF\xBD"));   // overlong
    CHECK_EQ(JsonEscape("a\"b\\c\n\t\x01"), "a\\\"b\\\\c\\n\\t\\u0001");
    CHECK_EQ(JsonString("é"), "\"é\"");
}

static void TestJsonShapeMatchesTheContract() {
    FakeUi ui;
    ui.Add(1, "frame", "App \"One\"");
    ui.Add(2, "check box", "Wi-Fi", 1);
    ui.info[2].checked = 1;
    ui.info[2].clickable = true;
    ui.Add(3, "entry", "Search", 1);
    ui.info[3].editable = true;
    ui.info[3].focused = true;
    Tree t = BuildTree(ui, 1, {});
    const std::string j = TreeJson(t);
    CHECK(j.front() == '[' && j.back() == ']');
    for (const char* key : {"\"id\":\"e1\"", "\"role\":\"window\"", "\"name\":\"App \\\"One\\\"\"", "\"text\":\"\"",
                            "\"bounds\":[10,20,100,30]", "\"enabled\":true", "\"focused\":true", "\"checked\":true",
                            "\"checked\":null", "\"clickable\":true", "\"scrollable\":false", "\"editable\":true",
                            "\"children\":[", "\"role\":\"checkbox\"", "\"role\":\"input\""})
        CHECK(j.find(key) != std::string::npos);
    CHECK_EQ(BoundsJson({1, 2, 3, 4}), "[1,2,3,4]");
    CHECK_EQ(Base64(""), "");
    CHECK_EQ(Base64("f"), "Zg==");
    CHECK_EQ(Base64("fo"), "Zm8=");
    CHECK_EQ(Base64("foo"), "Zm9v");
    CHECK_EQ(Base64("foobar"), "Zm9vYmFy");
    CHECK_EQ(JsonStringArray({"a", "b\"c"}), "[\"a\",\"b\\\"c\"]");
}

static void TestObservationIdsAreOnlyValidWithinTheirObservation() {
    std::vector<Handle> released;
    {
        ObservationStore store([&](Handle h) { released.push_back(h); });
        Handle h = 0;
        std::string err;
        CHECK(!store.Resolve("e1", "", h, err));
        CHECK_EQ(err, "no_observation");

        std::string obs1 = store.Begin({{"e1", 101}, {"e2", 102}});
        CHECK_EQ(obs1, "obs-1");
        CHECK(store.Resolve("e1", "", h, err) && h == 101);                 // no observation_id: the latest
        CHECK(store.Resolve("e2", obs1, h, err) && h == 102);
        CHECK(!store.Resolve("e9", obs1, h, err));
        CHECK_EQ(err, "unknown_element");

        std::string obs2 = store.Begin({{"e1", 201}});
        CHECK_EQ(obs2, "obs-2");
        CHECK_EQ(released.size(), 2u);                                       // the old observation's handles were released
        CHECK(!store.Resolve("e1", obs1, h, err));
        CHECK_EQ(err, "stale_observation");                                   // an id from the old one is refused clearly
        CHECK(!store.Resolve("e2", "", h, err));                              // e2 does not exist in obs-2
        CHECK_EQ(err, "unknown_element");
        CHECK(store.Resolve("e1", obs2, h, err) && h == 201);
    }
    CHECK_EQ(released.size(), 3u);                                            // the destructor releases the last one
}

static Params P(std::initializer_list<std::pair<const std::string, std::string>> l) { return Params(l); }

static void TestObserveParams() {
    ObserveArgs a;
    CHECK(ParseObserve({}, a).ok());
    CHECK(a.include_tree && !a.include_screenshot && a.limits.max_nodes == 150 && a.limits.max_depth == 12);
    CHECK(ParseObserve(P({{"include_screenshot", "true"}, {"max_nodes", "300"}, {"max_depth", "5"}}), a).ok());
    CHECK(a.include_screenshot && a.limits.max_nodes == 300 && a.limits.max_depth == 5);
    CHECK_EQ(ParseObserve(P({{"max_nodes", "0"}}), a).code, "invalid_max_nodes");
    CHECK_EQ(ParseObserve(P({{"max_nodes", "501"}}), a).code, "invalid_max_nodes");
    CHECK_EQ(ParseObserve(P({{"max_nodes", "abc"}}), a).code, "invalid_max_nodes");
    CHECK_EQ(ParseObserve(P({{"max_depth", "41"}}), a).code, "invalid_max_depth");
    CHECK_EQ(ParseObserve(P({{"include_tree", "maybe"}}), a).code, "invalid_include_tree");
    CHECK_EQ(ParseObserve(P({{"include_tree", "false"}}), a).code, "nothing_to_observe");
}

static void TestClickAndPressParams() {
    ClickArgs c;
    CHECK(ParseClick(P({{"element_id", "e5"}}), c).ok());
    CHECK(c.target.element_id == "e5" && c.button == "left" && c.count == 1 && !c.target.has_point);
    CHECK(ParseClick(P({{"x", "10.5"}, {"y", "20"}, {"button", "RIGHT"}, {"count", "2"}}), c).ok());
    CHECK(c.target.has_point && c.target.point.x == 10.5 && c.button == "right" && c.count == 2);
    CHECK(ParseClick(P({{"element_id", "e1"}, {"x", "1"}, {"y", "2"}}), c).ok());
    CHECK(c.target.element_id == "e1" && !c.target.has_point);                   // the element wins
    CHECK(ParseClick(P({{"element_id", "e1"}, {"observation_id", "obs-3"}}), c).ok());
    CHECK_EQ(c.target.observation_id, "obs-3");
    CHECK_EQ(ParseClick({}, c).code, "missing_target");
    CHECK_EQ(ParseClick(P({{"x", "1"}}), c).code, "missing_coordinates");
    CHECK_EQ(ParseClick(P({{"x", "a"}, {"y", "1"}}), c).code, "missing_coordinates");
    CHECK_EQ(ParseClick(P({{"x", "1e9"}, {"y", "1"}}), c).code, "invalid_coordinates");
    CHECK_EQ(ParseClick(P({{"element_id", "e1"}, {"button", "back"}}), c).code, "invalid_button");
    CHECK_EQ(ParseClick(P({{"element_id", "e1"}, {"count", "3"}}), c).code, "invalid_count");
    LongPressArgs l;
    CHECK(ParseLongPress(P({{"element_id", "e1"}}), l).ok() && l.duration_ms == 600);
    CHECK_EQ(ParseLongPress(P({{"element_id", "e1"}, {"duration_ms", "99"}}), l).code, "invalid_duration");
    CHECK_EQ(ParseLongPress(P({{"element_id", "e1"}, {"duration_ms", "5001"}}), l).code, "invalid_duration");
    CHECK_EQ(ParseLongPress({}, l).code, "missing_target");
}

static void TestTypeParams() {
    TypeArgs t;
    CHECK(ParseType(P({{"text", "hello\nworld\t!"}}), t).ok());
    CHECK(ParseType(P({{"text", "héllo ✓"}, {"element_id", "e2"}, {"clear_first", "true"}}), t).ok());
    CHECK(t.clear_first && t.element_id == "e2");
    CHECK_EQ(ParseType({}, t).code, "missing_required_param");
    CHECK_EQ(ParseType(P({{"text", "a\x01"}}), t).code, "invalid_text");                // control character
    CHECK_EQ(ParseType(P({{"text", "bad\xFF"}}), t).code, "invalid_text");              // not UTF-8
    CHECK_EQ(ParseType(P({{"text", std::string(501, 'a')}}), t).code, "text_too_long");
    CHECK(ParseType(P({{"text", std::string(500, 'a')}}), t).ok());
    CHECK_EQ(ParseType(P({{"text", "a"}, {"clear_first", "x"}}), t).code, "invalid_clear_first");
}

static void TestKeyParsing() {
    std::vector<int32_t> mods;
    int32_t key = 0;
    std::string spec;
    CHECK(ParseKey(P({{"keys", "ctrl+l"}}), mods, key, spec).ok());
    CHECK(mods.size() == 1 && mods[0] == 0xFFE3 && key == 'l' && spec == "ctrl+l");
    CHECK(ParseKey(P({{"keys", "Ctrl+Shift+T"}}), mods, key, spec).ok());
    CHECK(mods.size() == 2 && mods[1] == 0xFFE1 && key == 't');
    CHECK(ParseKey(P({{"keys", "alt+Tab"}}), mods, key, spec).ok() && mods[0] == 0xFFE9 && key == 0xFF09);
    CHECK(ParseKey(P({{"keys", "Return"}}), mods, key, spec).ok() && mods.empty() && key == 0xFF0D);
    CHECK(ParseKey(P({{"keys", "F5"}}), mods, key, spec).ok() && key == 0xFFBE + 4);
    CHECK(ParseKey(P({{"keys", "ctrl+plus"}}), mods, key, spec).ok() && key == '+');
    CHECK(ParseKey(P({{"keys", "shift"}}), mods, key, spec).ok() && key == 0xFFE1);     // a bare modifier is a key press
    CHECK(ParseKey(P({{"keys", "é"}}), mods, key, spec).ok() && key == 0xE9);
    CHECK(ParseKey(P({{"keys", "Page_Down"}}), mods, key, spec).ok() && key == 0xFF56);
    CHECK_EQ(ParseKey({}, mods, key, spec).code, "missing_required_param");
    CHECK_EQ(ParseKey(P({{"keys", "ctrl+"}}), mods, key, spec).code, "invalid_keys");
    CHECK_EQ(ParseKey(P({{"keys", "+a"}}), mods, key, spec).code, "invalid_keys");
    CHECK_EQ(ParseKey(P({{"keys", "hyper+a"}}), mods, key, spec).code, "unknown_modifier");
    CHECK_EQ(ParseKey(P({{"keys", "ctrl+nosuchkey"}}), mods, key, spec).code, "unknown_key");
    // unicode typing: Latin-1 is itself, the rest is 0x01000000 + code point
    int32_t sym = 0;
    CHECK(pai::keys::KeysymForChar(0x20AC, sym) && sym == 0x010020AC);
    CHECK(pai::keys::KeysymForChar('\n', sym) && sym == 0xFF0D);
    CHECK(!pai::keys::KeysymForChar(0x07, sym));
}

static void TestScrollAndSwipeParams() {
    ScrollArgs s;
    CHECK(ParseScroll(P({{"direction", "Down"}}), s).ok() && s.direction == "down" && s.amount == 3 && !s.target.has_point);
    CHECK(ParseScroll(P({{"direction", "up"}, {"amount", "10"}, {"element_id", "e3"}}), s).ok() && s.amount == 10);
    CHECK(ParseScroll(P({{"direction", "left"}, {"x", "5"}, {"y", "6"}}), s).ok() && s.target.has_point);
    CHECK_EQ(ParseScroll({}, s).code, "missing_required_param");
    CHECK_EQ(ParseScroll(P({{"direction", "sideways"}}), s).code, "invalid_direction");
    CHECK_EQ(ParseScroll(P({{"direction", "up"}, {"amount", "0"}}), s).code, "invalid_amount");
    CHECK_EQ(ParseScroll(P({{"direction", "up"}, {"amount", "51"}}), s).code, "invalid_amount");
    CHECK_EQ(ParseScroll(P({{"direction", "up"}, {"x", "5"}}), s).code, "missing_coordinates");

    SwipeArgs w;
    CHECK(ParseSwipe(P({{"from", "{\"x\": 10, \"y\": 20}"}, {"to", "{\"x\":30,\"y\":40}"}}), w).ok());
    CHECK(w.from.x == 10 && w.from.y == 20 && w.to.x == 30 && w.to.y == 40 && w.duration_ms == 300);
    CHECK(ParseSwipe(P({{"from_x", "1"}, {"from_y", "2"}, {"to_x", "3"}, {"to_y", "4"}, {"duration_ms", "800"}}), w).ok());
    CHECK(w.to.y == 4 && w.duration_ms == 800);
    CHECK(ParseSwipe(P({{"from.x", "1"}, {"from.y", "2"}, {"to.x", "3"}, {"to.y", "4"}}), w).ok());
    CHECK_EQ(ParseSwipe(P({{"from", "{\"x\":1,\"y\":2}"}}), w).code, "missing_required_param");
    CHECK_EQ(ParseSwipe({}, w).code, "missing_required_param");
    CHECK_EQ(ParseSwipe(P({{"from", "garbage"}, {"to", "{\"x\":1,\"y\":2}"}}), w).code, "invalid_from");
    CHECK_EQ(ParseSwipe(P({{"from", "{\"x\":1}"}, {"to", "{\"x\":1,\"y\":2}"}}), w).code, "invalid_from");
    CHECK_EQ(ParseSwipe(P({{"from", "{\"x\":1,\"y\":2}"}, {"to", "{\"x\":3,\"y\":\"no\"}"}}), w).code, "invalid_to");
    CHECK_EQ(ParseSwipe(P({{"from_x", "1"}, {"from_y", "2"}, {"to_x", "3"}, {"to_y", "4"}, {"duration_ms", "10"}}), w).code,
             "invalid_duration");
    std::map<std::string, std::string> o;
    CHECK(ParseFlatJsonObject("{\"a\": 1, \"b\": \"two\", \"c\": true}", o) && o["a"] == "1" && o["b"] == "two" && o["c"] == "true");
    CHECK(ParseFlatJsonObject("{}", o) && o.empty());
    CHECK(!ParseFlatJsonObject("{\"a\": {\"b\": 1}}", o));                    // nested is refused
    CHECK(!ParseFlatJsonObject("[1,2]", o));
    CHECK(!ParseFlatJsonObject("{\"a\" 1}", o));
}

static void TestFailResultShape() {
    std::string error;
    ResultMap r = FailResult(error, {"unknown_element", "call computer.observe first"}, {{"element_id", "e9"}});
    CHECK_EQ(error, "unknown_element");
    CHECK_EQ(r["ok"], "false");
    CHECK_EQ(r["error"], "unknown_element");
    CHECK_EQ(r["hint"], "call computer.observe first");
    CHECK_EQ(r["element_id"], "e9");
}

static void TestDesktopApps() {
    using namespace pai::apps;
    CHECK(SplitExec("gnome-calculator --new \"two words\" %U").size() == 4);
    Entry e;
    e.id = "org.gnome.Calculator";
    e.name = "Calculator";
    e.exec = SplitExec("gnome-calculator --x %U %% \"%c\"");
    auto argv = LaunchArgv(e);
    CHECK(argv.size() == 4 && argv[0] == "gnome-calculator" && argv[1] == "--x" && argv[2] == "%" && argv[3] == "Calculator");
    Entry h; h.id = "x.helper"; h.name = "Helper"; h.no_display = true;
    Entry f; f.id = "org.mozilla.firefox"; f.name = "Firefox Web Browser";
    std::vector<Entry> all = {e, h, f};
    CHECK_EQ(Filter(all, "", false).size(), 2u);                    // hidden helpers are not listed by default
    CHECK_EQ(Filter(all, "", true).size(), 3u);
    CHECK_EQ(Filter(all, "FIRE", false).size(), 1u);                 // case-insensitive, id or name
    CHECK_EQ(Filter(all, "calc", false)[0].id, "org.gnome.Calculator");
    Entry out;
    CHECK(FindEntry(all, "org.gnome.Calculator.desktop", out) && out.id == "org.gnome.Calculator");
    CHECK(FindEntry(all, "firefox web browser", out) && out.id == "org.mozilla.firefox");
    CHECK(FindEntry(all, "calculator", out) && out.id == "org.gnome.Calculator");     // last id segment
    CHECK(!FindEntry(all, "nothing", out));
}

int main() {
    TestRoles();
    TestTreeStructureAndFlattening();
    TestHiddenElementsAreSkippedWithTheirSubtree();
    TestNodeBudgetKeepsTheTopLevelControls();
    TestDepthLimit();
    TestPathologicalTreesAreBounded();
    TestTextIsBoundedAndSafe();
    TestJsonShapeMatchesTheContract();
    TestObservationIdsAreOnlyValidWithinTheirObservation();
    TestObserveParams();
    TestClickAndPressParams();
    TestTypeParams();
    TestKeyParsing();
    TestScrollAndSwipeParams();
    TestFailResultShape();
    TestDesktopApps();
    if (failures == 0) std::printf("ALL OK\n");
    return failures == 0 ? 0 : 1;
}
