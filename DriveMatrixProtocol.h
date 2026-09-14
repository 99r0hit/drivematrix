#ifndef DRIVEMATRIX_PROTOCOL_H
#define DRIVEMATRIX_PROTOCOL_H

#include <stdint.h>

enum PacketType
{
    PACKET_CONTROL = 1,
    PACKET_SET_NAME = 2,
    PACKET_CONFIG = 3,
    PACKET_TELEMETRY = 4,
    PACKET_RADIO_ACK = 5
};

enum ConfigItem
{
    CFG_STEERING_REVERSE = 1,

    CFG_STEERING_CENTER = 2,

    CFG_STEERING_LEFT = 3,

    CFG_STEERING_RIGHT = 4,

    CFG_IMU_FORWARD_AXIS = 10,

    CFG_IMU_RIGHT_AXIS = 11,

    CFG_IMU_UP_AXIS = 12,

    CFG_IMU_FORWARD_SIGN = 13,

    CFG_IMU_RIGHT_SIGN = 14,

    CFG_IMU_UP_SIGN = 15
};

struct __attribute__((packed))
ConfigPacket
{
    uint8_t type;

    uint8_t item;

    uint8_t value;
};

struct __attribute__((packed))
DiscoveryPacket
{
    char cmd[10];
};

struct __attribute__((packed))
DiscoveryResponse
{
    char receiverID[13];

    char vehicleName[19];
};

struct __attribute__((packed))
ControlPacket
{
    uint8_t type;
    uint16_t sequence;
    int16_t steering;
    int16_t throttle;
};

struct __attribute__((packed))
SetNamePacket
{
    uint8_t type;

    char receiverID[13];

    char vehicleName[18];
};

struct __attribute__((packed))
Motion3D
{
    int16_t x;

    int16_t y;

    int16_t z;
};

// Added __attribute__((packed)) to enforce strict 21-byte binary layout without compiler padding
struct __attribute__((packed)) TelemetryPacket {
    uint8_t type;     // Value = 4 (PACKET_TELEMETRY)
    uint32_t timestamp;
    float accelX;
    float accelY;
    float accelZ;
    float gyroX;
    float gyroY;
    float gyroZ;
};

struct __attribute__((packed))
RadioAckPacket
{
    uint8_t type;
    uint32_t sequence;
    uint8_t status;
    uint8_t failsafe;
};

#endif
