#pragma once
// Pure logic of the computer_control plugin: no AT-SPI, no X11, no portal. Everything here is unit-tested
// (tests/cc_unit_test.cpp) and the platform layers only feed it data.
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <vector>

namespace pai::cc {

using Params = std::map<std::string, std::string>;
using Handle = std::uintptr_t;               // opaque id of a UI element inside the platform layer

// ---------------------------------------------------------------------------------- geometry / text

struct Bounds {
    int x = 0, y = 0, w = 0, h = 0;
    bool Valid() const { return w > 0 && h > 0; }
};

std::string SanitizeUtf8(const std::string& s);                       // invalid bytes -> U+FFFD
std::string TruncateUtf8(const std::string& s, size_t max_chars);     // at a character boundary, adds "…"
std::string JsonEscape(const std::string& s);                         // sanitized + escaped, WITHOUT quotes
std::string JsonString(const std::string& s);                         // "…" with the escaping above
std::string Base64(const std::string& bytes);

// ------------------------------------------------------------------------------------------ roles

/// AT-SPI role name ("push button", "check box", ...) -> the contract's role set:
/// button link text input checkbox switch radio menu menu_item tab list list_item image window dialog scroll other
std::string NormalizeRole(const std::string& atspi_role, bool editable);

// -------------------------------------------------------------------------------------- the UI tree

struct NodeInfo {                                // what the platform layer reports about one element
    std::string role;                            // AT-SPI role name
    std::string name, text;
    Bounds bounds;
    bool showing = true, enabled = true, focused = false, editable = false, clickable = false, scrollable = false;
    int checked = -1;                            // -1 not checkable, 0 off, 1 on
};

class NodeSource {                               // implemented over AT-SPI; faked in the tests
public:
    virtual ~NodeSource() = default;
    virtual bool Info(Handle h, NodeInfo& out) = 0;
    virtual std::vector<Handle> Children(Handle h) = 0;
};

struct Limits {
    int max_nodes = 150;
    int max_depth = 12;
};

struct Node {
    std::string id, role, name, text;
    Bounds bounds;
    bool enabled = true, focused = false, clickable = false, scrollable = false, editable = false;
    int checked = -1;
    std::vector<Node> children;
};

struct Tree {
    std::vector<Node> roots;
    int count = 0;
    bool truncated = false;
    std::map<std::string, Handle> handles;       // element id -> platform handle (this observation only)
};

/// Breadth-first (so the top-level controls always make it in) with a node budget and a depth limit.
/// Hidden elements are skipped with their subtree; purely structural containers (no name, no text, not
/// interactive) are spliced out so the tree holds what a person would call "controls".
Tree BuildTree(NodeSource& source, Handle root, const Limits& limits);

std::string NodeJson(const Node& n);
std::string TreeJson(const Tree& t);
std::string BoundsJson(const Bounds& b);

// ------------------------------------------------------------------------- observations / ids

/// Element ids are only valid inside the observation that produced them. The newest observation wins.
class ObservationStore {
public:
    explicit ObservationStore(std::function<void(Handle)> release = {}) : release_(std::move(release)) {}
    ~ObservationStore() { Clear(); }
    ObservationStore(const ObservationStore&) = delete;
    ObservationStore& operator=(const ObservationStore&) = delete;

    /// Make `tree` the current observation; returns its id ("obs-N"). Releases the previous handles.
    std::string Begin(const std::map<std::string, Handle>& handles);
    void Clear();
    /// error: no_observation | stale_observation | unknown_element
    bool Resolve(const std::string& element_id, const std::string& observation_id, Handle& out, std::string& error) const;
    const std::string& current() const { return current_; }

private:
    std::function<void(Handle)> release_;
    std::string current_;
    std::map<std::string, Handle> handles_;
    uint64_t counter_ = 0;
};

// ------------------------------------------------------------------------------------- parameters

struct Fail {
    std::string code;                            // machine readable
    std::string hint;                            // what to do about it
    bool ok() const { return code.empty(); }
};

struct Point { double x = 0, y = 0; };

struct ObserveArgs { bool include_tree = true, include_screenshot = false; Limits limits; };
struct Target { std::string element_id, observation_id; bool has_point = false; Point point; };
struct ClickArgs { Target target; std::string button = "left"; int count = 1; };
struct LongPressArgs { Target target; int duration_ms = 600; };
struct TypeArgs { std::string text; std::string element_id, observation_id; bool clear_first = false; };
struct ScrollArgs { Target target; std::string direction; int amount = 3; };
struct SwipeArgs { Point from, to; int duration_ms = 300; };

constexpr size_t kMaxTextChars = 500;
constexpr int kMaxScrollAmount = 50;

Fail ParseObserve(const Params& p, ObserveArgs& out);
Fail ParseClick(const Params& p, ClickArgs& out);
Fail ParseLongPress(const Params& p, LongPressArgs& out);
Fail ParseType(const Params& p, TypeArgs& out);
Fail ParseKey(const Params& p, std::vector<int32_t>& modifiers, int32_t& key, std::string& spec);
Fail ParseScroll(const Params& p, ScrollArgs& out);
Fail ParseSwipe(const Params& p, SwipeArgs& out);

/// A flat JSON object of numbers/strings/booleans, e.g. {"x": 10, "y": 20}. False if it is anything else.
bool ParseFlatJsonObject(const std::string& s, std::map<std::string, std::string>& out);

// ------------------------------------------------------------------------------------ result shape

using ResultMap = std::map<std::string, std::string>;

ResultMap FailResult(std::string& error, const Fail& f, ResultMap extra = {});
std::string JsonStringArray(const std::vector<std::string>& v);

}  // namespace pai::cc
