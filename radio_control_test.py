import serial
import time


PORT = "/dev/ttyUSB0"
BAUD_RATE = 115200


def send_command(ser, command):
    print(f">>> {command}")

    ser.write((command + "\n").encode())
    ser.flush()

    deadline = time.time() + 1.0

    while time.time() < deadline:

        line = ser.readline().decode(
            errors="replace"
        ).strip()

        if line:
            print(f"<<< {line}")

            if line.startswith("TX_OK:"):
                break


ser = serial.Serial(
    PORT,
    BAUD_RATE,
    timeout=0.1
)

# Opening the Nano resets it.
time.sleep(2)

ser.reset_input_buffer()

print("Nano connected:", PORT)

send_command(
    ser,
    "CONTROL,1000,2000"
)

time.sleep(1)

send_command(
    ser,
    "CONTROL,-1500,3000"
)

ser.close()

print("Test complete")
