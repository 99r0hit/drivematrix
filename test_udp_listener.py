import socket

def listen_udp():
    IP = "0.0.0.0"
    PORT = 5005
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((IP, PORT))
    sock.settimeout(1.0) # 1 second timeout so we can catch keyboard interrupts easily

    print(f"📡 Listening for raw UDP packets on {IP}:{PORT}...")
    print("👉 If nothing prints when you move your IMU, your hardware is not transmitting to this IP/Port.")

    try:
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                print(f"📦 Received {len(data)} bytes from {addr}: {data}")
            except socket.timeout:
                continue
    except KeyboardInterrupt:
        print("\n🛑 Stopping listener...")
    finally:
        sock.close()

if __name__ == "__main__":
    listen_udp()
