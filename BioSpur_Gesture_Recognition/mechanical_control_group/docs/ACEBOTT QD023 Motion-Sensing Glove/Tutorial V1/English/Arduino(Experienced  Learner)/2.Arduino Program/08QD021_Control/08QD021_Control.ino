#include <ACB_BLEDevice.h>
#include <ACB_Biped_Robot_WiFi.h>
#include <WiFi.h>

#define FIRMWARE_VERSION "20250304 V1.0"
#define SERVICE_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"         // Unique identifier for the service
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"  // Unique identifier for the characteristic

ACB_Biped_Robot_WiFi Biped_Robot;  // Biped_Robot object
ACB_BLEDevice ble(SERVICE_UUID,CHARACTERISTIC_UUID);    // Ble object

/*
         ---------------
        |     O   O     |
        |---------------|
YR 18==> |             | <== YL 5   180-0   180-0
         ---------------
            ||     ||
            ||     ||
RR 17==> ------   ------ <== RL 16  180-0  180-0
         |-----   -----|
*/

void setup() {
  Serial.begin(115200);                  // Start the serial communication for debugging
  ble.Bluetooth_Connected("ESP32-BLE");  // Bluetooth connection

  Biped_Robot.myservo_init(5, 16, 17, 18);  // YL RL RR YR
}

void sendCommand(int key) {
  ble.ClientData = "";    // Clear data
  switch (key) {
    case 1:
      Biped_Robot.left_kick();
      break;
    case 2:
      Biped_Robot.right_kick();
      break;
    case 3:
      Biped_Robot.left_tilt();
      break;
    case 4:
      Biped_Robot.right_tilt();
      break;
    case 5:
      Biped_Robot.left_ankles();
      break;
    case 6:
      Biped_Robot.right_ankles();
      break;
    case 7:
      Biped_Robot.left_stamp();
      break;
    case 8:
      Biped_Robot.right_stamp();
      break;
    case 9:
      Biped_Robot.sprint();
      break;
    case 10:
      Biped_Robot.dance();
      break;
  }

  String status = "True";
  ble.ClientSend(status);
};


void loop() {
  ble.ClientReceived();
  if (!ble.connected) {
    Biped_Robot.stop();   // stop 
  }
  // If connected and characteristic is writable, send data
  if (ble.connected && ble.pRemoteCharacteristic && ble.pRemoteCharacteristic->canWrite()) {
    delay(5);
    if (strcmp(ble.ClientData.c_str(), "command11") == 0) {
      Biped_Robot.forward();
    } else if (strcmp(ble.ClientData.c_str(), "command12") == 0) {
      Biped_Robot.backward();
    } else if (strcmp(ble.ClientData.c_str(), "command15") == 0) {
      Biped_Robot.leftward();
    } else if (strcmp(ble.ClientData.c_str(), "command16") == 0) {
      Biped_Robot.rightward();
    } else if (strcmp(ble.ClientData.c_str(), "command0") == 0) {
      Biped_Robot.stop();
    } else if (strcmp(ble.ClientData.c_str(), "command1") == 0) {
      sendCommand(1);
    } else if (strcmp(ble.ClientData.c_str(), "command2") == 0) {
      sendCommand(2);
    } else if (strcmp(ble.ClientData.c_str(), "command3") == 0) {
      sendCommand(3);
    } else if (strcmp(ble.ClientData.c_str(), "command4") == 0) {
      sendCommand(4);
    } else if (strcmp(ble.ClientData.c_str(), "command5") == 0) {
      sendCommand(5);
    } else if (strcmp(ble.ClientData.c_str(), "command6") == 0) {
      sendCommand(6);
    } else if (strcmp(ble.ClientData.c_str(), "command7") == 0) {
      sendCommand(7);
    } else if (strcmp(ble.ClientData.c_str(), "command8") == 0) {
      sendCommand(8);
    } else if (strcmp(ble.ClientData.c_str(), "command9") == 0) {
      sendCommand(9);
    } else if (strcmp(ble.ClientData.c_str(), "command10") == 0) {
      sendCommand(10);
    } else {
      Biped_Robot.stop();
    }
  }
}