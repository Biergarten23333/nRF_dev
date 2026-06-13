#include <ACB_BLEDevice.h>
#include <vehicle.h>
#include <ESP32Servo.h>

#define SERVICE_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"         // Unique identifier for the service
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"  // Unique identifier for the characteristic

vehicle Acebott;      // Car object
ACB_BLEDevice ble(SERVICE_UUID,CHARACTERISTIC_UUID);    // Ble object
Servo turnServo;

#define LED_Module1 2      // led1 PIN
#define LED_Module2 12     // led2 PIN
#define Shoot_PIN 32       // Shoot PIN
#define TURN_SERVO_PIN 26  // Servo PIN

int angle = 90;  // Servo Initial Angle

void Servo_Move(int angles)  //servo control
{
  int pwmValue = map(angles, 1, 180, 115, 70);  // Map 1-180 degrees
  int currentPwm = turnServo.read();
  if (pwmValue > currentPwm) {
    for (int j = 0; j < 20; j++) {
      int newPwm = currentPwm + (pwmValue - currentPwm) * (j / 20.0);
      turnServo.write(newPwm);
      delay(20);
    }
  } else {
    for (int j = 0; j < 15; j++) {
      int newPwm = currentPwm + (pwmValue - currentPwm) * (j / 15.0);
      turnServo.write(newPwm);
      delay(15);
    }
  }
}

void setup() {
  Serial.begin(115200);  // Start the serial communication for debugging

  ble.Bluetooth_Connected("ESP32-BLE");   // bluetooth connected

  pinMode(Shoot_PIN, OUTPUT);
  pinMode(LED_Module1, OUTPUT);  // Set pin of LED module as output
  pinMode(LED_Module2, OUTPUT);
  turnServo.attach(TURN_SERVO_PIN);  // Connect the servo to the TURN_SERVO_PIN pin
  turnServo.write(angle);
  Acebott.Init();         // Initialize Acebott
  Acebott.Move(Stop, 0);  // Stop the Acebott exercise
}

int getStep(String key, int value, int step, int max, int min, bool type) {
  if (type) {
    value += step;
    if (value > max) {
      value = max;
    }
  } else {
    value -= step;
    if (value < min) {
      value = min;
    }
  }
  return value;
}

void sendCommand(String key, int value, bool type) {
  int step = 15;
  // Serial.println(key);
  if (key == "angle") {
    angle = getStep(key, value, step, 180, 0, type);
    if (angle>=180 && angle<=0){
      return;
    }
    Servo_Move(angle);
  } 
  String status = "True";
  ble.ClientSend(status);
};


void loop() {

  ble.ClientReceived();   // Received data

  if (!ble.connected) {
    Acebott.Move(Stop, 0);    // stop
  }

  // Serial.println(ble.ClientData);

  // If connected and characteristic is writable, send data
  if (ble.connected && ble.pRemoteCharacteristic && ble.pRemoteCharacteristic->canWrite()) {
    delay(5);
    if (strcmp(ble.ClientData.c_str(), "command11") == 0) {
      Acebott.Move(Forward, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command12") == 0) {
      Acebott.Move(Backward, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command15") == 0) {
      Acebott.Move(Move_Left, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command16") == 0) {
      Acebott.Move(Move_Right, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command13") == 0) {
      Acebott.Move(Contrarotate, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command14") == 0) {
      Acebott.Move(Clockwise, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command0") == 0) {
      Acebott.Move(Stop, 0);
    } else if (strcmp(ble.ClientData.c_str(), "command5") == 0) {
      sendCommand("angle", angle, false);
    } else if (strcmp(ble.ClientData.c_str(), "command6") == 0) {
      sendCommand("angle", angle, true);
    } else if (strcmp(ble.ClientData.c_str(), "command17") == 0) {
      digitalWrite(Shoot_PIN, HIGH);
      delay(200);
      digitalWrite(Shoot_PIN, LOW);
    } 
    ble.ClientData = "";
  }
}