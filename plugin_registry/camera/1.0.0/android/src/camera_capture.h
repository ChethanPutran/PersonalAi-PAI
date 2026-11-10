#pragma once

#include <map>
#include <string>

namespace pai::camera {

std::map<std::string, std::string> capture(const std::map<std::string, std::string>& params);

}  // namespace pai::camera