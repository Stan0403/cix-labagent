// Experimental fixed-grid CIX vision bridge, isolated from the production build.
#include <cerrno>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

static bool codex_socket_transfer(int fd, void * data, size_t size, bool writing) {
    auto * bytes = static_cast<unsigned char *>(data);
    while (size > 0) {
        const ssize_t count = writing ? send(fd, bytes, size, MSG_NOSIGNAL) : recv(fd, bytes, size, 0);
        if (count < 0 && errno == EINTR) { continue; }
        if (count <= 0) { return false; }
        bytes += count;
        size -= count;
    }
    return true;
}

static void codex_clip_dump(const clip_ctx * ctx, const clip_image_f32 & img, const float * output) {
    const char * prefix = std::getenv("LABAGENT_CLIP_DUMP_PREFIX");
    if (!prefix || !*prefix || !output) { return; }
    const size_t output_size = clip_n_output_tokens(ctx, const_cast<clip_image_f32 *>(&img)) * clip_n_mmproj_embd(ctx);
    std::ofstream input(std::string(prefix) + ".input.f32", std::ios::binary);
    input.write(reinterpret_cast<const char *>(img.buf.data()), img.buf.size() * sizeof(float));
    std::ofstream encoded(std::string(prefix) + ".output.f32", std::ios::binary);
    encoded.write(reinterpret_cast<const char *>(output), output_size * sizeof(float));
    std::ofstream metadata(std::string(prefix) + ".json");
    metadata << "{\"width\":" << img.nx << ",\"height\":" << img.ny << ",\"output_floats\":" << output_size << "}";
}

static bool codex_clip_npu(const clip_ctx * ctx, const clip_image_f32 & img, float * output, const char * path) {
    if (ctx->model.proj_type != PROJECTOR_TYPE_QWEN3VL || img.nx != 320 || img.ny != 320 ||
        img.buf.size() != 320u * 320 * 3 || clip_n_mmproj_embd(ctx) != 4 * 2560 || !output) {
        LOG_ERR("codex_npu_vision: requires Qwen3-VL-4B, 320x320, four 2560-feature outputs\n");
        return false;
    }
    struct sockaddr_un address = {};
    address.sun_family = AF_UNIX;
    if (std::strlen(path) >= sizeof(address.sun_path)) { return false; }
    std::strcpy(address.sun_path, path);
    const int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) { return false; }
    struct close_socket { int fd; ~close_socket() { close(fd); } } guard{fd};
    struct timeval timeout = {30, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    if (connect(fd, reinterpret_cast<struct sockaddr *>(&address), sizeof(address)) != 0) {
        LOG_ERR("codex_npu_vision: cannot connect to local encoder worker\n");
        return false;
    }
    uint32_t request[4] = {0x51334e50, 320, 320, static_cast<uint32_t>(img.buf.size())};
    uint32_t response[2] = {};
    if (!codex_socket_transfer(fd, request, sizeof(request), true) ||
        !codex_socket_transfer(fd, const_cast<float *>(img.buf.data()), img.buf.size() * sizeof(float), true) ||
        !codex_socket_transfer(fd, response, sizeof(response), false) ||
        response[0] != request[0] || response[1] != 100u * 2560 * 4 ||
        !codex_socket_transfer(fd, output, response[1] * sizeof(float), false)) {
        LOG_ERR("codex_npu_vision: encoder protocol failed; inference rejected\n");
        return false;
    }
    for (uint32_t i = 0; i < response[1]; ++i) {
        if (!std::isfinite(output[i])) { return false; }
    }
    LOG_INF("codex_npu_vision: encoded 100 tokens with four feature streams via CIX NPU\n");
    codex_clip_dump(ctx, img, output);
    return true;
}
