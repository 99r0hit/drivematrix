#include <iostream>
#include "DriveMatrixProtocol.h"

int main()
{
    std::cout << "DriveMatrix Protocol Sizes\n";
    std::cout << "-------------------------\n";

    std::cout << "ConfigPacket:       "
              << sizeof(ConfigPacket) << " bytes\n";

    std::cout << "DiscoveryPacket:    "
              << sizeof(DiscoveryPacket) << " bytes\n";

    std::cout << "DiscoveryResponse:  "
              << sizeof(DiscoveryResponse) << " bytes\n";

    std::cout << "ControlPacket:      "
              << sizeof(ControlPacket) << " bytes\n";

    std::cout << "SetNamePacket:      "
              << sizeof(SetNamePacket) << " bytes\n";

    std::cout << "Motion3D:            "
              << sizeof(Motion3D) << " bytes\n";

    std::cout << "TelemetryPacket:     "
              << sizeof(TelemetryPacket) << " bytes\n";

    std::cout << "RadioAckPacket:      "
              << sizeof(RadioAckPacket) << " bytes\n";

    return 0;
}
