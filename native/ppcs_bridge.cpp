#include <arpa/inet.h>
#include <CommonCrypto/CommonDigest.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <exception>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "PPCS_API.h"
#include "PPCS_Error.h"

namespace {

volatile sig_atomic_t g_stop = 0;

void on_signal(int) {
    g_stop = 1;
}

std::string json_escape(const std::string &value) {
    std::ostringstream out;
    for (unsigned char ch : value) {
        switch (ch) {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\b':
                out << "\\b";
                break;
            case '\f':
                out << "\\f";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                if (ch < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(ch) << std::dec;
                } else {
                    out << static_cast<char>(ch);
                }
                break;
        }
    }
    return out.str();
}

void print_json_line(const std::string &phase, const std::vector<std::pair<std::string, std::string>> &fields) {
    std::cout << "{\"phase\":\"" << json_escape(phase) << "\"";
    for (const auto &field : fields) {
        std::cout << ",\"" << json_escape(field.first) << "\":\"" << json_escape(field.second) << "\"";
    }
    std::cout << "}" << std::endl;
}

std::string error_name(int code) {
    if (code > 0) {
        return "NON_ERROR_POSITIVE_RESULT";
    }
    switch (code) {
        case 0:
            return "ERROR_PPCS_SUCCESSFUL";
        case -1:
            return "ERROR_PPCS_NOT_INITIALIZED";
        case -2:
            return "ERROR_PPCS_ALREADY_INITIALIZED";
        case -3:
            return "ERROR_PPCS_TIME_OUT";
        case -4:
            return "ERROR_PPCS_INVALID_ID";
        case -5:
            return "ERROR_PPCS_INVALID_PARAMETER";
        case -6:
            return "ERROR_PPCS_DEVICE_NOT_ONLINE";
        case -7:
            return "ERROR_PPCS_FAIL_TO_RESOLVE_NAME";
        case -8:
            return "ERROR_PPCS_INVALID_PREFIX";
        case -9:
            return "ERROR_PPCS_ID_OUT_OF_DATE";
        case -10:
            return "ERROR_PPCS_NO_RELAY_SERVER_AVAILABLE";
        case -11:
            return "ERROR_PPCS_INVALID_SESSION_HANDLE";
        case -12:
            return "ERROR_PPCS_SESSION_CLOSED_REMOTE";
        case -13:
            return "ERROR_PPCS_SESSION_CLOSED_TIMEOUT";
        case -14:
            return "ERROR_PPCS_SESSION_CLOSED_CALLED";
        case -15:
            return "ERROR_PPCS_REMOTE_SITE_BUFFER_FULL";
        case -16:
            return "ERROR_PPCS_USER_LISTEN_BREAK";
        case -17:
            return "ERROR_PPCS_MAX_SESSION";
        case -18:
            return "ERROR_PPCS_UDP_PORT_BIND_FAILED";
        case -19:
            return "ERROR_PPCS_USER_CONNECT_BREAK";
        case -20:
            return "ERROR_PPCS_SESSION_CLOSED_INSUFFICIENT_MEMORY";
        case -21:
            return "ERROR_PPCS_INVALID_APILICENSE";
        case -22:
            return "ERROR_PPCS_FAIL_TO_CREATE_THREAD";
        default:
            return "UNKNOWN_ERROR";
    }
}

std::string bytes_to_hex(const std::vector<unsigned char> &data, size_t limit = 0) {
    std::ostringstream out;
    const size_t end = (limit > 0 && limit < data.size()) ? limit : data.size();
    for (size_t idx = 0; idx < end; ++idx) {
        out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(data[idx]);
    }
    return out.str();
}

std::vector<unsigned char> hex_to_bytes(std::string value) {
    value.erase(remove_if(value.begin(), value.end(), [](unsigned char ch) {
        return ch == ' ' || ch == '\n' || ch == '\r' || ch == '\t' || ch == ':';
    }), value.end());

    if (value.size() % 2 != 0) {
        throw std::runtime_error("hex payload length must be even");
    }

    std::vector<unsigned char> out;
    out.reserve(value.size() / 2);
    for (size_t i = 0; i < value.size(); i += 2) {
        const auto piece = value.substr(i, 2);
        char *end = nullptr;
        const long parsed = strtol(piece.c_str(), &end, 16);
        if (end == nullptr || *end != '\0' || parsed < 0 || parsed > 255) {
            throw std::runtime_error("invalid hex payload");
        }
        out.push_back(static_cast<unsigned char>(parsed));
    }
    return out;
}

constexpr int VAVA_TAG_APP_CMD = -352321533;
constexpr int VAVA_TAG_VIDEO = -352321519;
constexpr int VAVA_TAG_AUDIO = -352321502;

struct CommandRequest {
    int cmd = -1;
    std::string json = "{}";
};

struct AvFrameHeader {
    int tag = 0;
    int encodetype = 0;
    int frametype = 0;
    int framerate = 0;
    int res = 0;
    int size = 0;
    uint64_t ntsamp = 0;
    int framenum = 0;
    int width = 0;
    int height = 0;
};

std::vector<unsigned char> int32_to_le_bytes(int value) {
    std::vector<unsigned char> out(4);
    out[0] = static_cast<unsigned char>(value & 0xFF);
    out[1] = static_cast<unsigned char>((value >> 8) & 0xFF);
    out[2] = static_cast<unsigned char>((value >> 16) & 0xFF);
    out[3] = static_cast<unsigned char>((value >> 24) & 0xFF);
    return out;
}

int le_bytes_to_int32(const unsigned char *data) {
    return static_cast<int>(data[0])
        | (static_cast<int>(data[1]) << 8)
        | (static_cast<int>(data[2]) << 16)
        | (static_cast<int>(data[3]) << 24);
}

std::vector<unsigned char> build_command_packet(int cmd_code, const std::string &json_payload) {
    auto sync_bytes = int32_to_le_bytes(VAVA_TAG_APP_CMD);
    auto cmd_bytes = int32_to_le_bytes(cmd_code);
    auto len_bytes = int32_to_le_bytes(static_cast<int>(json_payload.size()));
    std::vector<unsigned char> out;
    out.reserve(12 + json_payload.size());
    out.insert(out.end(), sync_bytes.begin(), sync_bytes.end());
    out.insert(out.end(), cmd_bytes.begin(), cmd_bytes.end());
    out.insert(out.end(), len_bytes.begin(), len_bytes.end());
    out.insert(out.end(), json_payload.begin(), json_payload.end());
    return out;
}

std::string md5_hex(const std::string &value) {
    unsigned char digest[CC_MD5_DIGEST_LENGTH];
    CC_MD5(value.data(), static_cast<CC_LONG>(value.size()), digest);
    std::ostringstream out;
    for (unsigned char byte : digest) {
        out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte);
    }
    return out.str();
}

uint64_t decode_ntsamp(int seconds_part, int millis_part) {
    if (seconds_part < 0) {
        return 0;
    }
    if (millis_part < 0) {
        millis_part = 0;
    }
    return static_cast<uint64_t>(seconds_part) * 1000ULL + static_cast<uint64_t>(millis_part % 1000);
}

std::pair<int, int> resolution_to_wh(int res) {
    switch (res) {
        case 0:
        case 4:
            return {1920, 1080};
        case 1:
        case 5:
            return {1280, 720};
        case 2:
        case 6:
            return {640, 360};
        default:
            return {0, 0};
    }
}

AvFrameHeader parse_av_header(const unsigned char *data) {
    AvFrameHeader header;
    header.tag = le_bytes_to_int32(data + 0);
    header.encodetype = static_cast<int>(data[4]);
    header.frametype = static_cast<int>(data[5]);
    header.framerate = static_cast<int>(data[6]);
    header.res = static_cast<int>(data[7]);
    header.size = le_bytes_to_int32(data + 8);
    header.ntsamp = decode_ntsamp(le_bytes_to_int32(data + 12), le_bytes_to_int32(data + 16));
    header.framenum = le_bytes_to_int32(data + 20);
    if (header.tag == VAVA_TAG_VIDEO) {
        const auto [width, height] = resolution_to_wh(header.res);
        header.width = width;
        header.height = height;
    }
    return header;
}

std::string av_tag_name(int tag) {
    switch (tag) {
        case VAVA_TAG_VIDEO:
            return "video";
        case VAVA_TAG_AUDIO:
            return "audio";
        default:
            return "unknown";
    }
}

size_t find_annexb_start_code(const std::vector<unsigned char> &payload) {
    for (size_t i = 0; i + 3 < payload.size(); ++i) {
        if (payload[i] == 0x00 && payload[i + 1] == 0x00) {
            if (payload[i + 2] == 0x01) {
                return i + 3;
            }
            if (i + 4 < payload.size() && payload[i + 2] == 0x00 && payload[i + 3] == 0x01) {
                return i + 4;
            }
        }
    }
    return std::string::npos;
}

std::string guess_video_codec(const std::vector<unsigned char> &payload) {
    const size_t offset = find_annexb_start_code(payload);
    if (offset == std::string::npos || offset >= payload.size()) {
        return "unknown";
    }

    const unsigned char nal = payload[offset];
    const int h264_type = nal & 0x1F;
    const int h265_type = (nal >> 1) & 0x3F;

    if (h265_type == 32 || h265_type == 33 || h265_type == 34 || h265_type == 19 || h265_type == 20) {
        return "H265";
    }
    if (h264_type == 7 || h264_type == 8 || h264_type == 5 || h264_type == 1) {
        return "H264";
    }
    return "unknown";
}

std::string guess_audio_codec(const std::vector<unsigned char> &payload, int encodetype) {
    if (payload.size() >= 2 && payload[0] == 0xFF && (payload[1] & 0xF0) == 0xF0) {
        return "AAC_ADTS";
    }
    switch (encodetype) {
        case 3:
            return "AAC_or_G711_vendor";
        case 1:
            return "AAC_or_vendor";
        default:
            return "unknown";
    }
}

CommandRequest parse_command_request(const std::string &value) {
    CommandRequest request;
    const size_t colon = value.find(':');
    if (colon == std::string::npos) {
        request.cmd = std::stoi(value);
        request.json = "{}";
        return request;
    }

    request.cmd = std::stoi(value.substr(0, colon));
    request.json = value.substr(colon + 1);
    return request;
}

bool ppcs_read_exact(int session, int channel, unsigned char *buffer, int size, int timeout_ms, int &ret_out) {
    INT32 needed = size;
    ret_out = PPCS_Read(
        session,
        static_cast<UCHAR>(channel),
        reinterpret_cast<CHAR *>(buffer),
        &needed,
        static_cast<UINT32>(timeout_ms)
    );
    return ret_out >= 0;
}

struct Options {
    std::string target_id;
    std::string init_string;
    bool connect_by_server = true;
    int lan_search = 0x7E;
    int udp_port = 0;
    int write_channel = 0;
    int read_channel = 0;
    int read_size = 4096;
    int read_timeout_ms = 2000;
    int read_count = 0;
    int hold_seconds = 0;
    std::optional<std::vector<unsigned char>> write_bytes;
    int send_cmd = -1;
    std::string json_data;
    std::vector<CommandRequest> command_requests;
    int cmd_delay_ms = 300;
    int read_cmd_count = 0;
    int read_av_count = 0;
    int av_channel = 1;
    int av_payload_prefix = 64;
    std::string video_out_path;
    std::string audio_out_path;
    std::optional<std::string> auth_session_key;
};

void usage(const char *argv0) {
    std::cerr
        << "Usage: " << argv0 << " --target-id <didCode> --init-string <initCode> [options]\n"
        << "  --direct                 Use PPCS_Connect instead of PPCS_ConnectByServer\n"
        << "  --lan-search <int>       Default 126\n"
        << "  --udp-port <int>         Default 0\n"
        << "  --write-channel <int>    Default 0\n"
        << "  --write-hex <hex>        Raw hex payload to write once\n"
        << "  --write-text <text>      UTF-8 text payload to write once\n"
        << "  --read-channel <int>     Default 0\n"
        << "  --read-size <int>        Default 4096\n"
        << "  --read-timeout-ms <int>  Default 2000\n"
        << "  --read-count <int>       How many reads to attempt\n"
        << "  --send-cmd <int>         VAVA command code, written as 12-byte header + JSON\n"
        << "  --json-data <json>       JSON body used with --send-cmd\n"
        << "  --cmd <cmd[:json]>       Repeatable command sequence, e.g. 1:{\"channel\":0}\n"
        << "  --cmd-delay-ms <int>     Delay between commands, default 300\n"
        << "  --read-cmd-count <int>   Read parsed command responses from channel 0\n"
        << "  --read-av-count <int>    Read and parse AV frames from live channel\n"
        << "  --av-channel <int>       Default 1\n"
        << "  --av-payload-prefix <n>  Include first n payload bytes in output, default 64\n"
        << "  --video-out <path>       Append raw Annex-B H264/H265 payloads to file/fifo\n"
        << "  --audio-out <path>       Append raw AAC/ADTS payloads to file/fifo\n"
        << "  --auth-session-key <k>   Send cmd=0 auth using the provided sessionKey\n"
        << "  --hold-seconds <int>     Keep session open after I/O\n";
}

Options parse_args(int argc, char **argv) {
    Options opts;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto need_value = [&](const char *name) -> std::string {
            if (i + 1 >= argc) {
                throw std::runtime_error(std::string("missing value for ") + name);
            }
            return argv[++i];
        };

        if (arg == "--target-id" || arg == "--did") {
            opts.target_id = need_value(arg.c_str());
        } else if (arg == "--init-string" || arg == "--init-code") {
            opts.init_string = need_value(arg.c_str());
        } else if (arg == "--direct") {
            opts.connect_by_server = false;
        } else if (arg == "--lan-search") {
            opts.lan_search = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--udp-port") {
            opts.udp_port = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--write-channel") {
            opts.write_channel = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--read-channel") {
            opts.read_channel = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--read-size") {
            opts.read_size = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--read-timeout-ms") {
            opts.read_timeout_ms = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--read-count") {
            opts.read_count = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--hold-seconds") {
            opts.hold_seconds = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--write-hex") {
            opts.write_bytes = hex_to_bytes(need_value(arg.c_str()));
        } else if (arg == "--write-text") {
            const std::string value = need_value(arg.c_str());
            opts.write_bytes = std::vector<unsigned char>(value.begin(), value.end());
        } else if (arg == "--send-cmd") {
            opts.send_cmd = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--json-data") {
            opts.json_data = need_value(arg.c_str());
        } else if (arg == "--cmd") {
            opts.command_requests.push_back(parse_command_request(need_value(arg.c_str())));
        } else if (arg == "--cmd-delay-ms") {
            opts.cmd_delay_ms = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--read-cmd-count") {
            opts.read_cmd_count = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--read-av-count") {
            opts.read_av_count = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--av-channel") {
            opts.av_channel = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--av-payload-prefix") {
            opts.av_payload_prefix = std::stoi(need_value(arg.c_str()));
        } else if (arg == "--video-out") {
            opts.video_out_path = need_value(arg.c_str());
        } else if (arg == "--audio-out") {
            opts.audio_out_path = need_value(arg.c_str());
        } else if (arg == "--auth-session-key") {
            opts.auth_session_key = need_value(arg.c_str());
        } else if (arg == "--help" || arg == "-h") {
            usage(argv[0]);
            exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }

    if (opts.target_id.empty()) {
        throw std::runtime_error("missing --target-id");
    }
    if (opts.init_string.empty()) {
        throw std::runtime_error("missing --init-string");
    }
    if (opts.read_size <= 0) {
        throw std::runtime_error("--read-size must be > 0");
    }
    if (opts.send_cmd >= 0 && opts.json_data.empty()) {
        opts.json_data = "{}";
    }
    if (opts.av_payload_prefix < 0) {
        throw std::runtime_error("--av-payload-prefix must be >= 0");
    }
    return opts;
}

}  // namespace

int main(int argc, char **argv) {
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    try {
        const Options opts = parse_args(argc, argv);

        print_json_line(
            "start",
            {
                {"targetId", opts.target_id},
                {"connectMode", opts.connect_by_server ? "server" : "direct"},
                {"lanSearch", std::to_string(opts.lan_search)},
                {"udpPort", std::to_string(opts.udp_port)},
            }
        );

        std::vector<char> init_buffer(opts.init_string.begin(), opts.init_string.end());
        init_buffer.push_back('\0');

        int ret = PPCS_Initialize(init_buffer.data());
        print_json_line(
            "initialize",
            {
                {"ret", std::to_string(ret)},
                {"name", error_name(ret)},
            }
        );
        if (ret != ERROR_PPCS_SUCCESSFUL && ret != ERROR_PPCS_ALREADY_INITIALIZED) {
            return 2;
        }

        st_PPCS_NetInfo netinfo{};
        ret = PPCS_NetworkDetect(&netinfo, static_cast<UINT16>(opts.udp_port));
        print_json_line(
            "network_detect",
            {
                {"ret", std::to_string(ret)},
                {"name", error_name(ret)},
                {"internet", std::to_string(netinfo.bFlagInternet)},
                {"hostResolved", std::to_string(netinfo.bFlagHostResolved)},
                {"serverHello", std::to_string(netinfo.bFlagServerHello)},
                {"natType", std::to_string(netinfo.NAT_Type)},
                {"wanIp", netinfo.MyWanIP},
                {"lanIp", netinfo.MyLanIP},
            }
        );

        int session = -1;
        if (opts.connect_by_server) {
            std::vector<char> server_buffer(opts.init_string.begin(), opts.init_string.end());
            server_buffer.push_back('\0');
            session = PPCS_ConnectByServer(
                opts.target_id.c_str(),
                static_cast<CHAR>(opts.lan_search),
                static_cast<UINT16>(opts.udp_port),
                server_buffer.data()
            );
        } else {
            session = PPCS_Connect(
                opts.target_id.c_str(),
                static_cast<CHAR>(opts.lan_search),
                static_cast<UINT16>(opts.udp_port)
            );
        }

        print_json_line(
            "connect",
            {
                {"session", std::to_string(session)},
                {"name", error_name(session)},
            }
        );
        if (session < 0) {
            PPCS_DeInitialize();
            return 3;
        }

        st_PPCS_Session session_info{};
        ret = PPCS_Check(session, &session_info);
        if (ret == ERROR_PPCS_SUCCESSFUL) {
            print_json_line(
                "session_info",
                {
                    {"ret", std::to_string(ret)},
                    {"mode", session_info.bMode == 0 ? "P2P" : "RLY"},
                    {"socket", std::to_string(session_info.Skt)},
                    {"remote", std::string(inet_ntoa(session_info.RemoteAddr.sin_addr)) + ":" + std::to_string(ntohs(session_info.RemoteAddr.sin_port))},
                    {"local", std::string(inet_ntoa(session_info.MyLocalAddr.sin_addr)) + ":" + std::to_string(ntohs(session_info.MyLocalAddr.sin_port))},
                    {"wan", std::string(inet_ntoa(session_info.MyWanAddr.sin_addr)) + ":" + std::to_string(ntohs(session_info.MyWanAddr.sin_port))},
                    {"connectTime", std::to_string(session_info.ConnectTime)},
                    {"did", session_info.DID},
                    {"role", session_info.bCorD == 0 ? "client" : "device"},
                }
            );
        } else {
            print_json_line(
                "session_info",
                {
                    {"ret", std::to_string(ret)},
                    {"name", error_name(ret)},
                }
            );
        }

        std::ofstream video_out;
        std::ofstream audio_out;
        if (!opts.video_out_path.empty()) {
            video_out.open(opts.video_out_path, std::ios::binary | std::ios::out);
            if (!video_out.is_open()) {
                throw std::runtime_error("failed to open --video-out path");
            }
            video_out.rdbuf()->pubsetbuf(nullptr, 0);
        }
        if (!opts.audio_out_path.empty()) {
            audio_out.open(opts.audio_out_path, std::ios::binary | std::ios::out);
            if (!audio_out.is_open()) {
                throw std::runtime_error("failed to open --audio-out path");
            }
            audio_out.rdbuf()->pubsetbuf(nullptr, 0);
        }

        if (opts.write_bytes.has_value()) {
            const auto &payload = opts.write_bytes.value();
            ret = PPCS_Write(
                session,
                static_cast<UCHAR>(opts.write_channel),
                reinterpret_cast<CHAR *>(const_cast<unsigned char *>(payload.data())),
                static_cast<INT32>(payload.size())
            );
            print_json_line(
                "write",
                {
                    {"channel", std::to_string(opts.write_channel)},
                    {"size", std::to_string(payload.size())},
                    {"ret", std::to_string(ret)},
                    {"name", error_name(ret)},
                    {"hex", bytes_to_hex(payload, 256)},
                }
            );
        }

        if (opts.auth_session_key.has_value()) {
            std::mt19937 rng(static_cast<unsigned int>(std::chrono::steady_clock::now().time_since_epoch().count()));
            std::uniform_int_distribution<int> dist(100000, 999999);
            const int auth_random = dist(rng);
            const std::string auth_value = md5_hex("vava:" + std::to_string(auth_random) + ":2017");
            const std::string auth_json =
                "{\"random\":" + std::to_string(auth_random) +
                ",\"auth\":\"" + auth_value +
                "\",\"key\":\"" + opts.auth_session_key.value() + "\"}";
            const auto auth_packet = build_command_packet(0, auth_json);
            ret = PPCS_Write(
                session,
                static_cast<UCHAR>(0),
                reinterpret_cast<CHAR *>(const_cast<unsigned char *>(auth_packet.data())),
                static_cast<INT32>(auth_packet.size())
            );
            print_json_line(
                "auth_cmd",
                {
                    {"random", std::to_string(auth_random)},
                    {"auth", auth_value},
                    {"key", opts.auth_session_key.value()},
                    {"ret", std::to_string(ret)},
                    {"name", error_name(ret)},
                    {"json", auth_json},
                    {"hex", bytes_to_hex(auth_packet, 512)},
                }
            );

            unsigned char header[12] = {0};
            int read_ret = 0;
            if (ppcs_read_exact(session, 0, header, 12, opts.read_timeout_ms, read_ret)) {
                const int sync_code = le_bytes_to_int32(header);
                const int cmd_code = le_bytes_to_int32(header + 4);
                const int cmd_length = le_bytes_to_int32(header + 8);
                std::vector<unsigned char> body;
                body.resize(cmd_length > 0 ? static_cast<size_t>(cmd_length) : 0);
                if (cmd_length > 0 && ppcs_read_exact(session, 0, body.data(), cmd_length, opts.read_timeout_ms, read_ret)) {
                    std::string json_payload(body.begin(), body.end());
                    print_json_line(
                        "auth_resp",
                        {
                            {"ret", "0"},
                            {"name", error_name(0)},
                            {"syncCode", std::to_string(sync_code)},
                            {"cmd", std::to_string(cmd_code)},
                            {"cmdLength", std::to_string(cmd_length)},
                            {"json", json_payload},
                            {"hex", bytes_to_hex(body, 512)},
                        }
                    );
                } else {
                    print_json_line(
                        "auth_resp",
                        {
                            {"ret", std::to_string(read_ret)},
                            {"name", error_name(read_ret)},
                            {"syncCode", std::to_string(sync_code)},
                            {"cmd", std::to_string(cmd_code)},
                            {"cmdLength", std::to_string(cmd_length)},
                        }
                    );
                }
            } else {
                print_json_line(
                    "auth_resp",
                    {
                        {"ret", std::to_string(read_ret)},
                        {"name", error_name(read_ret)},
                    }
                );
            }
        }

        std::vector<CommandRequest> command_requests = opts.command_requests;
        if (opts.send_cmd >= 0) {
            command_requests.push_back(CommandRequest{opts.send_cmd, opts.json_data});
        }

        for (size_t cmd_index = 0; cmd_index < command_requests.size() && !g_stop; ++cmd_index) {
            const auto &request = command_requests[cmd_index];
            const auto packet = build_command_packet(request.cmd, request.json);
            ret = PPCS_Write(
                session,
                static_cast<UCHAR>(0),
                reinterpret_cast<CHAR *>(const_cast<unsigned char *>(packet.data())),
                static_cast<INT32>(packet.size())
            );
            print_json_line(
                "send_cmd",
                {
                    {"index", std::to_string(cmd_index)},
                    {"cmd", std::to_string(request.cmd)},
                    {"json", request.json},
                    {"ret", std::to_string(ret)},
                    {"name", error_name(ret)},
                    {"hex", bytes_to_hex(packet, 512)},
                }
            );
            if (opts.cmd_delay_ms > 0 && cmd_index + 1 < command_requests.size() && !g_stop) {
                std::this_thread::sleep_for(std::chrono::milliseconds(opts.cmd_delay_ms));
            }
        }

        for (int cmd_idx = 0; cmd_idx < opts.read_cmd_count && !g_stop; ++cmd_idx) {
            unsigned char header[12] = {0};
            int read_ret = 0;
            if (!ppcs_read_exact(session, 0, header, 12, opts.read_timeout_ms, read_ret)) {
                print_json_line(
                    "read_cmd",
                    {
                        {"index", std::to_string(cmd_idx)},
                        {"stage", "header"},
                        {"ret", std::to_string(read_ret)},
                        {"name", error_name(read_ret)},
                    }
                );
                continue;
            }
            const int sync_code = le_bytes_to_int32(header);
            const int cmd_code = le_bytes_to_int32(header + 4);
            const int cmd_length = le_bytes_to_int32(header + 8);
            std::vector<unsigned char> body;
            body.resize(cmd_length > 0 ? static_cast<size_t>(cmd_length) : 0);
            if (cmd_length > 0) {
                if (!ppcs_read_exact(session, 0, body.data(), cmd_length, opts.read_timeout_ms, read_ret)) {
                    print_json_line(
                        "read_cmd",
                        {
                            {"index", std::to_string(cmd_idx)},
                            {"stage", "body"},
                            {"ret", std::to_string(read_ret)},
                            {"name", error_name(read_ret)},
                            {"syncCode", std::to_string(sync_code)},
                            {"cmd", std::to_string(cmd_code)},
                            {"cmdLength", std::to_string(cmd_length)},
                        }
                    );
                    continue;
                }
            }
            std::string json_payload(body.begin(), body.end());
            print_json_line(
                "read_cmd",
                {
                    {"index", std::to_string(cmd_idx)},
                    {"ret", "0"},
                    {"name", error_name(0)},
                    {"syncCode", std::to_string(sync_code)},
                    {"cmd", std::to_string(cmd_code)},
                    {"cmdLength", std::to_string(cmd_length)},
                    {"json", json_payload},
                    {"hex", bytes_to_hex(body, 512)},
                }
            );
        }

        for (int av_idx = 0; (opts.read_av_count < 0 || av_idx < opts.read_av_count) && !g_stop; ++av_idx) {
            unsigned char av_header_raw[24] = {0};
            int read_ret = 0;
            if (!ppcs_read_exact(session, opts.av_channel, av_header_raw, 24, opts.read_timeout_ms, read_ret)) {
                print_json_line(
                    "read_av",
                    {
                        {"index", std::to_string(av_idx)},
                        {"channel", std::to_string(opts.av_channel)},
                        {"stage", "header"},
                        {"ret", std::to_string(read_ret)},
                        {"name", error_name(read_ret)},
                    }
                );
                continue;
            }

            const AvFrameHeader av_header = parse_av_header(av_header_raw);
            if (av_header.size < 0 || av_header.size > 8 * 1024 * 1024) {
                print_json_line(
                    "read_av",
                    {
                        {"index", std::to_string(av_idx)},
                        {"channel", std::to_string(opts.av_channel)},
                        {"stage", "validate"},
                        {"tag", std::to_string(av_header.tag)},
                        {"tagName", av_tag_name(av_header.tag)},
                        {"size", std::to_string(av_header.size)},
                        {"error", "invalid_frame_size"},
                    }
                );
                break;
            }

            std::vector<unsigned char> payload(static_cast<size_t>(av_header.size));
            if (av_header.size > 0) {
                if (!ppcs_read_exact(session, opts.av_channel, payload.data(), av_header.size, opts.read_timeout_ms, read_ret)) {
                    print_json_line(
                        "read_av",
                        {
                            {"index", std::to_string(av_idx)},
                            {"channel", std::to_string(opts.av_channel)},
                            {"stage", "payload"},
                            {"ret", std::to_string(read_ret)},
                            {"name", error_name(read_ret)},
                            {"tag", std::to_string(av_header.tag)},
                            {"tagName", av_tag_name(av_header.tag)},
                            {"size", std::to_string(av_header.size)},
                        }
                    );
                    continue;
                }
            }

            std::string codec_guess = "unknown";
            if (av_header.tag == VAVA_TAG_VIDEO) {
                codec_guess = guess_video_codec(payload);
                if (video_out.is_open() && !payload.empty()) {
                    video_out.write(reinterpret_cast<const char *>(payload.data()), static_cast<std::streamsize>(payload.size()));
                    video_out.flush();
                }
            } else if (av_header.tag == VAVA_TAG_AUDIO) {
                codec_guess = guess_audio_codec(payload, av_header.encodetype);
                if (audio_out.is_open() && !payload.empty()) {
                    audio_out.write(reinterpret_cast<const char *>(payload.data()), static_cast<std::streamsize>(payload.size()));
                    audio_out.flush();
                }
            }

            print_json_line(
                "read_av",
                {
                    {"index", std::to_string(av_idx)},
                    {"channel", std::to_string(opts.av_channel)},
                    {"tag", std::to_string(av_header.tag)},
                    {"tagName", av_tag_name(av_header.tag)},
                    {"encodetype", std::to_string(av_header.encodetype)},
                    {"frametype", std::to_string(av_header.frametype)},
                    {"framerate", std::to_string(av_header.framerate)},
                    {"res", std::to_string(av_header.res)},
                    {"width", std::to_string(av_header.width)},
                    {"height", std::to_string(av_header.height)},
                    {"size", std::to_string(av_header.size)},
                    {"ntsamp", std::to_string(av_header.ntsamp)},
                    {"framenum", std::to_string(av_header.framenum)},
                    {"codecGuess", codec_guess},
                    {"payloadHex", bytes_to_hex(payload, static_cast<size_t>(opts.av_payload_prefix))},
                }
            );
        }

        for (int read_idx = 0; read_idx < opts.read_count && !g_stop; ++read_idx) {
            std::vector<unsigned char> buffer(static_cast<size_t>(opts.read_size));
            INT32 size = static_cast<INT32>(buffer.size());
            ret = PPCS_Read(
                session,
                static_cast<UCHAR>(opts.read_channel),
                reinterpret_cast<CHAR *>(buffer.data()),
                &size,
                static_cast<UINT32>(opts.read_timeout_ms)
            );
            if (ret >= 0) {
                buffer.resize(static_cast<size_t>(ret));
            } else if (size > 0 && size <= static_cast<INT32>(buffer.size())) {
                buffer.resize(static_cast<size_t>(size));
            } else {
                buffer.clear();
            }
            print_json_line(
                "read",
                {
                    {"index", std::to_string(read_idx)},
                    {"channel", std::to_string(opts.read_channel)},
                    {"ret", std::to_string(ret)},
                    {"name", error_name(ret)},
                    {"reportedSize", std::to_string(size)},
                    {"hex", bytes_to_hex(buffer, 512)},
                }
            );
        }

        for (int second = 0; second < opts.hold_seconds && !g_stop; ++second) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }

        ret = PPCS_Close(session);
        print_json_line(
            "close",
            {
                {"ret", std::to_string(ret)},
                {"name", error_name(ret)},
            }
        );
        ret = PPCS_DeInitialize();
        print_json_line(
            "deinitialize",
            {
                {"ret", std::to_string(ret)},
                {"name", error_name(ret)},
            }
        );
        return 0;
    } catch (const std::exception &exc) {
        print_json_line("fatal", {{"error", exc.what()}});
        return 1;
    }
}
