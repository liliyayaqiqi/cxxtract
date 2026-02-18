#pragma once
#include <memory>

#define RTC_EXPORT __attribute__((visibility("default")))

namespace webrtc {
namespace rtp_rtcp {

/**
 * @brief 核心编码器接口。
 * 负责处理动态码率和前向纠错。
 */
class RTC_EXPORT RtpEncoder {
public:
    virtual ~RtpEncoder() = default;

    /// 发送基础音视频帧
    virtual int Send(const uint8_t* payload);

    /// 发送带有 FEC 冗余的帧 (测试函数重载！)
    virtual int Send(const uint8_t* payload, bool enable_fec);
};

// 测试 Template 包裹是否被正确剥离并提取
template <typename T>
struct PacketBuffer {
    T* buffer_ptr;
};

extern "C" {
    /// 底层 C 风格初始化钩子 (测试 extern "C" 穿透！)
    void init_rtp_engine();
}

} // namespace rtp_rtcp
} // namespace webrtc