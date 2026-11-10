#include "camera_record.h"

#include <stdexcept>

namespace pai::camera {

std::map<std::string, std::string> record(const std::map<std::string, std::string>& /*params*/) {
    // Video recording requires combining:
    //   - ACameraDevice with a repeating request at TEMPLATE_RECORD
    //   - AMediaCodec H.264 encoder
    //   - AMediaMuxer to write MP4
    //   - an ANativeWindow input surface for the encoder
    //
    // This is a substantial implementation and is left as a follow-up.
    // The ABI contract is honoured: the plugin is loaded, capabilities
    // are advertised, and capture works. Recording returns a structured
    // error the host can surface to the user.
    return {
        {"ok", "false"},
        {"error", "record_not_implemented_yet"},
    };
}

}  // namespace pai::camera