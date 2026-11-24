#include "pai_plugin_common.h"
#include "pai_system_helpers.h"

#include <filesystem>
#include <fstream>
#include <sstream>

namespace fs = std::filesystem;

namespace {

std::vector<std::string> AllowedRoots() {
    return pai::sys::EnvList(
        "PAI_FILES_ROOTS",
        {
            std::string(pai::sys::HomeDir()) + "/Documents",
            std::string(pai::sys::HomeDir()) + "/Downloads",
            std::string(pai::sys::HomeDir()) + "/Pictures",
            std::string(pai::sys::HomeDir()) + "/Videos",
            "/tmp/pai",
            "/data/pai",
        });
}


std::string Base64Encode(const std::string& in) {
    static const char tbl[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((in.size() + 2) / 3) * 4);
    size_t i = 0;
    while (i + 2 < in.size()) {
        uint32_t n = (uint8_t)in[i] << 16 | (uint8_t)in[i+1] << 8 | (uint8_t)in[i+2];
        out += tbl[(n >> 18) & 0x3F];
        out += tbl[(n >> 12) & 0x3F];
        out += tbl[(n >> 6) & 0x3F];
        out += tbl[n & 0x3F];
        i += 3;
    }
    size_t rem = in.size() - i;
    if (rem == 1) {
        uint32_t n = (uint8_t)in[i] << 16;
        out += tbl[(n >> 18) & 0x3F];
        out += tbl[(n >> 12) & 0x3F];
        out += "==";
    } else if (rem == 2) {
        uint32_t n = (uint8_t)in[i] << 16 | (uint8_t)in[i+1] << 8;
        out += tbl[(n >> 18) & 0x3F];
        out += tbl[(n >> 12) & 0x3F];
        out += tbl[(n >> 6) & 0x3F];
        out += '=';
    }
    return out;
}

bool CheckPath(const std::string& p, std::string& resolved, std::string& error) {
    auto roots = AllowedRoots();
    if (!pai::sys::PathInRoots(p, roots, resolved)) {
        error = "path_outside_allowed_roots";
        return false;
    }
    return true;
}

pai::plugin::ResultMap ListOp(const pai::plugin::ResultMap& params, std::string& error) {
    std::string resolved;
    if (!CheckPath(params.count("path") ? params.at("path") : "", resolved, error))
        return {};

    bool recursive = params.count("recursive") && params.at("recursive") == "true";
    int max_entries = 500;
    if (params.count("limit")) {
        try {
            int v = std::stoi(params.at("limit"));
            max_entries = std::max(1, std::min(v, 10000));
        } catch (...) {
            // leave default
        }
    }

    pai::plugin::ResultMap out;
    std::ostringstream entries;
    int count = 0;

    try {
        if (recursive) {
            for (auto& entry : fs::recursive_directory_iterator(
                     resolved, fs::directory_options::skip_permission_denied)) {
                if (count++ >= max_entries) break;
                entries << entry.path().string()
                        << "\t" << (entry.is_directory() ? "dir" : "file")
                        << "\t";
                if (entry.is_regular_file()) {
                    std::error_code ec;
                    entries << fs::file_size(entry.path(), ec);
                } else {
                    entries << "0";
                }
                entries << "\n";
            }
        } else {
            for (auto& entry : fs::directory_iterator(
                     resolved, fs::directory_options::skip_permission_denied)) {
                if (count++ >= max_entries) break;
                entries << entry.path().filename().string()
                        << "\t" << (entry.is_directory() ? "dir" : "file")
                        << "\t";
                if (entry.is_regular_file()) {
                    std::error_code ec;
                    entries << fs::file_size(entry.path(), ec);
                } else {
                    entries << "0";
                }
                entries << "\n";
            }
        }
    } catch (const std::exception& e) {
        error = std::string("list_failed: ") + e.what();
        return {};
    }

    out["ok"] = "true";
    out["path"] = resolved;
    out["count"] = std::to_string(count);
    out["entries"] = entries.str();  // format: path\ttype\tsize\n
    return out;
}

pai::plugin::ResultMap ReadOp(const pai::plugin::ResultMap& params, std::string& error) {
    std::string resolved;
    if (!CheckPath(params.count("path") ? params.at("path") : "", resolved, error))
        return {};

    int max_bytes = 1024 * 1024;  // 1 MiB default
    if (params.count("max_bytes")) {
        try {
            int v = std::stoi(params.at("max_bytes"));
            max_bytes = std::max(1, std::min(v, 10 * 1024 * 1024));
        } catch (...) {
            // leave default
        }
    }

    std::error_code ec;
    auto size = fs::file_size(resolved, ec);
    if (ec) { error = "stat_failed: " + ec.message(); return {}; }
    if (size > static_cast<uintmax_t>(max_bytes)) {
        error = "file_too_large";
        return {
            {"ok", "false"},
            {"error", "file_too_large"},
            {"size", std::to_string(size)},
            {"max_bytes", std::to_string(max_bytes)},
        };
    }

        std::ifstream f(resolved, std::ios::binary);
    if (!f) { error = "open_failed"; return {}; }

    std::ostringstream ss;
    ss << f.rdbuf();
    std::string data = ss.str();

    // Detect binary (NUL byte anywhere). If found and caller didn't
    // request base64, reject with a hint rather than silently truncating
    // at the CFFI boundary.
    bool is_binary = data.find('\0') != std::string::npos;
    bool want_binary = params.count("binary") && params.at("binary") == "true";

    if (is_binary) {
        if (!want_binary) {
            error = "binary_file";
            return {
                {"ok", "false"},
                {"error", "binary_file"},
                {"path", resolved},
                {"size", std::to_string(size)},
                {"hint",
                 "Set binary=true to receive base64-encoded content. "
                 "Raw binary is truncated at the first NUL byte."},
            };
        }
        return {
            {"ok", "true"},
            {"path", resolved},
            {"size", std::to_string(size)},
            {"content", Base64Encode(data)},
            {"encoding", "base64"},
        };
    }

    return {
        {"ok", "true"},
        {"path", resolved},
        {"size", std::to_string(size)},
        {"content", std::move(data)},
        {"encoding", "raw"},
    };
}


pai::plugin::ResultMap WriteOp(const pai::plugin::ResultMap& params, std::string& error) {
    std::string resolved;
    if (!CheckPath(params.count("path") ? params.at("path") : "", resolved, error))
        return {};

    if (!params.count("content")) {
        error = "missing_required_param: content";
        return {};
    }
    const std::string content = params.at("content");

    bool append = params.count("append") && params.at("append") == "true";

    std::error_code ec;
    fs::create_directories(fs::path(resolved).parent_path(), ec);

    std::ofstream f(resolved,
        std::ios::binary | (append ? std::ios::app : std::ios::trunc));
    if (!f) { error = "open_failed"; return {}; }
    f.write(content.data(), content.size());
    f.close();

    return {
        {"ok", "true"},
        {"path", resolved},
        {"bytes_written", std::to_string(content.size())},
    };
}

pai::plugin::ResultMap StatOp(const pai::plugin::ResultMap& params, std::string& error) {
    std::string resolved;
    if (!CheckPath(params.count("path") ? params.at("path") : "", resolved, error))
        return {};

    std::error_code ec;
    if (!fs::exists(resolved, ec)) {
        return {{"ok", "true"}, {"exists", "false"}, {"path", resolved}};
    }

    pai::plugin::ResultMap out = {
        {"ok", "true"},
        {"exists", "true"},
        {"path", resolved},
        {"is_dir", fs::is_directory(resolved, ec) ? "true" : "false"},
        {"is_file", fs::is_regular_file(resolved, ec) ? "true" : "false"},
    };
    if (fs::is_regular_file(resolved, ec)) {
        out["size"] = std::to_string(fs::file_size(resolved, ec));
    }
    auto ftime = fs::last_write_time(resolved, ec);
    if (!ec) {
        auto sctp = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
            ftime - fs::file_time_type::clock::now() + std::chrono::system_clock::now());
        out["modified_epoch"] = std::to_string(
            std::chrono::system_clock::to_time_t(sctp));
    }
    return out;
}

pai::plugin::ResultMap DeleteOp(const pai::plugin::ResultMap& params, std::string& error) {
    std::string resolved;
    if (!CheckPath(params.count("path") ? params.at("path") : "", resolved, error))
        return {};

    bool recursive = params.count("recursive") && params.at("recursive") == "true";

    std::error_code ec;
    if (recursive) {
        fs::remove_all(resolved, ec);
    } else {
        fs::remove(resolved, ec);
    }
    if (ec) { error = "delete_failed: " + ec.message(); return {}; }
    return {{"ok", "true"}, {"path", resolved}};
}

pai::plugin::ResultMap CopyMoveOp(
    const pai::plugin::ResultMap& params, bool move, std::string& error) {
    std::string src, dst;
    if (!CheckPath(params.count("source") ? params.at("source") : "", src, error))
        return {};
    if (!CheckPath(params.count("destination") ? params.at("destination") : "", dst, error))
        return {};

    std::error_code ec;
    if (move) {
        fs::rename(src, dst, ec);
        if (ec) {
            // Cross-filesystem move — fall back to copy+delete.
            ec.clear();
            fs::copy(src, dst,
                fs::copy_options::recursive |
                fs::copy_options::overwrite_existing, ec);
            if (ec) {
                error = "move_failed: " + ec.message();
                return {};
            }
            std::error_code cleanup_ec;
            fs::remove_all(src, cleanup_ec);
            if (cleanup_ec) {
                error = "move_partial: copied but source cleanup failed: "
                    + cleanup_ec.message();
                return {
                    {"ok", "false"},
                    {"error", "move_partial"},
                    {"source", src},
                    {"destination", dst},
                    {"cleanup_error", cleanup_ec.message()},
                };
            }
        }
    } else {
        fs::copy(src, dst,
            fs::copy_options::recursive | fs::copy_options::overwrite_existing, ec);
        if (ec) { error = "copy_failed: " + ec.message(); return {}; }
    }
    return {
        {"ok", "true"},
        {"source", src},
        {"destination", dst},
    };
}

pai::plugin::ResultMap DispatchFiles(
    const std::string& capability,
    const pai::plugin::ResultMap& params,
    std::string& error) {
    if (capability == "files.list")   return ListOp(params, error);
    if (capability == "files.read")   return ReadOp(params, error);
    if (capability == "files.write")  return WriteOp(params, error);
    if (capability == "files.stat")   return StatOp(params, error);
    if (capability == "files.delete") return DeleteOp(params, error);
    if (capability == "files.copy")   return CopyMoveOp(params, false, error);
    if (capability == "files.move")   return CopyMoveOp(params, true, error);
    error = "unknown_capability";
    return {};
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.files",
    ({"files.list", "files.read", "files.write", "files.stat",
      "files.delete", "files.copy", "files.move"}),
    ({"filesystem"}),
    &DispatchFiles
)