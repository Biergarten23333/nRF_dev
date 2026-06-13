#include <ACB_BLEDevice.h>
#include <vehicle.h>
#include <ACB_Servo.h>
#include <ACB_Truckbott.h>

#define SERVICE_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"         // Unique identifier for the service
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"  // Unique identifier for the characteristic

ACB_Servo servo_17;
ACB_Servo servo_18;
ACB_Truckbott truckbot;
ACB_BLEDevice ble(SERVICE_UUID,CHARACTERISTIC_UUID);    // Ble object

char Forklift_Angle = 60;
char Tipping_Angle = 90;

bool servo1Moving = false;
int servo1Direction = 0;  
bool servo2Moving = false;
int servo2Direction = 0;  

void setup() {
  Serial.begin(115200);  // Start the serial communication for debugging
  ble.Bluetooth_Connected("ESP32-BLE");   // bluetooth connected
  truckbot.motorControl(0, 0);  // Stop the Acebott exercise

  servo_17.setpin(17);
  servo_17.allocateTimer(3);
  servo_18.setpin(18);
  servo_18.allocateTimer(2);

  servo_17.run(Forklift_Angle);
  servo_18.run(Tipping_Angle);
}

void loop() {
  ble.ClientReceived();   // Received data
  if (!ble.connected) {
    truckbot.motorControl(0, 0);    // stop
    servo1Moving = false;  // Stop the servo when the connection is disconnected
    servo1Direction = 0;   // Reset the direction
    servo2Moving = false;  // Stop the second servo
    servo2Direction = 0;   // Reset the direction
  }
  // If connected and characteristic is writable, send data
  if (ble.connected && ble.pRemoteCharacteristic && ble.pRemoteCharacteristic->canWrite()) {
    delay(5);
    if (strcmp(ble.ClientData.c_str(), "command11") == 0) {
      truckbot.motorControl(255, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command12") == 0) {
      truckbot.motorControl(-255, -255);
    } else if (strcmp(ble.ClientData.c_str(), "command15") == 0) {
      truckbot.motorControl(-120, 170);
    } else if (strcmp(ble.ClientData.c_str(), "command16") == 0) {
      truckbot.motorControl(170, -120);
    } else if (strcmp(ble.ClientData.c_str(), "command1") == 0) {
      servo1Moving = true;
      servo1Direction = -1;
    }
    else if (strcmp(ble.ClientData.c_str(), "command2") == 0) {
      servo1Moving = true;
      servo1Direction = 1;

    } else if (strcmp(ble.ClientData.c_str(), "command5") == 0) {
      servo2Moving = true;
      servo2Direction = 1;
    } else if (strcmp(ble.ClientData.c_str(), "command6") == 0) {
      servo2Moving = true;
      servo2Direction = -1;
    }  
    else if (strcmp(ble.ClientData.c_str(), "command0") == 0) {
      truckbot.motorControl(0, 0);
      servo1Moving = false;  // Stop the movement of the servo
      servo1Direction = 0;   // Reset the direction
      servo2Moving = false;  // Stop the second servo
      servo2Direction = 0;   // Reset the direction
    }
    ble.ClientData = "";
  }

  if (servo1Moving) {
    if (servo1Direction == 1) {

      if (Tipping_Angle < 90) {
        Tipping_Angle += 5;
        Tipping_Angle = constrain(Tipping_Angle, 50, 90);
        servo_18.run(Tipping_Angle);
      } else {

        servo1Moving = false;
      }
    } else if (servo1Direction == -1) {

      if (Tipping_Angle > 50) {
        Tipping_Angle -= 5;
        Tipping_Angle = constrain(Tipping_Angle, 50, 90);
        servo_18.run(Tipping_Angle);
      } else {

        servo1Moving = false;
      }
    }
    delay(100);  
  }

  if (servo2Moving) {
    if (servo2Direction == 1) {

      if (Forklift_Angle < 180) {
        Forklift_Angle += 10;
        Forklift_Angle = constrain(Forklift_Angle, 60, 180);
        servo_17.run(Forklift_Angle);
      } else {

        servo2Moving = false;
      }
    } else if (servo2Direction == -1) {

      if (Forklift_Angle > 60) {
        Forklift_Angle -= 10;
        Forklift_Angle = constrain(Forklift_Angle, 60, 180);
        servo_17.run(Forklift_Angle);
      } else {

        servo2Moving = false;
      }
    }
    delay(100); 
  }
}