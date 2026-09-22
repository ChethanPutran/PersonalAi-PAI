#pragma once

#include <string>
#include <map>

namespace pai::camera {

/// Capture a single still frame from /dev/video* and write it as JPEG.
/// Returns a result map; throws std::runtime_error on unrecoverable failure.
std::map<std::string, std::string> capture(const std::map<std::string, std::string>& params);

}  // namespace pai::camera