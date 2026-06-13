#include <ACB_BLEDevice.h>
#include <vehicle.h>

#define SERVICE_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"         // Unique identifier for the service
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"  // Unique identifier for the characteristic

vehicle Acebott;      // Car object
ACB_BLEDevice ble(SERVICE_UUID,CHARACTERISTIC_UUID);    // Ble object

void setup() {
  Serial.begin(115200);  // Start the serial communication for debugging
  ble.Bluetooth_Connected("ESP32-BLE");   // bluetooth connected
  Acebott.Init();         // Initialize Acebott
  Acebott.Move(Stop, 0);  // Stop the Acebott exercise
}

void loop() {
  ble.ClientReceived();   // Received data
  if (!ble.connected) {
    Acebott.Move(Stop, 0);    // stop
  }
  // If connected and characteristic is writable, send data
  if (ble.connected && ble.pRemoteCharacteristic && ble.pRemoteCharacteristic->canWrite()) {
    delay(5);
    if (strcmp(ble.ClientData.c_str(), "command11") == 0) {
      Acebott.Move(Forward, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command12") == 0) {
      Acebott.Move(Backward, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command13") == 0) {
      Acebott.Move(Contrarotate, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command14") == 0) {
      Acebott.Move(Clockwise, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command0") == 0) {
      Acebott.Move(Stop, 0);
    }
    ble.ClientData = "";
  }
}