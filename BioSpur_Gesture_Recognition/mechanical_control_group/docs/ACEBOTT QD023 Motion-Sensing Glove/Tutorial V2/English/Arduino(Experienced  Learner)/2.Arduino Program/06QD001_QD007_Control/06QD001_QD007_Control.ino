#include <ACB_BLEDevice.h>
#include <ESP32Servo.h>
#include <ACB_CAR_ARM.h>

#define SERVICE_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"         // Unique identifier for the service
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"  // Unique identifier for the characteristic

ACB_CAR_ARM CAR_ARM;  // Arm object
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

  ble.Bluetooth_Connected("ESP32-BLE");

  pinMode(Shoot_PIN, OUTPUT);
  pinMode(LED_Module1, OUTPUT);  // Set pin of LED module as output
  pinMode(LED_Module2, OUTPUT);
  turnServo.attach(TURN_SERVO_PIN);  // Connect the servo to the TURN_SERVO_PIN pin
  turnServo.write(angle);
  CAR_ARM.Vehicle_init();         // Initialize Acebott
  CAR_ARM.Vehicle_Move(Stop, 0);  // Stop the Acebott exercise

  CAR_ARM.ARM_init(25, 26, 27, 33, 4);  // servo initialize
}

// Initial Servo Angle
int chassis = 90;
int shoulder = 40;
int elbow = 50;
int claws = 90;
int wrist = 90;

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
  Serial.println(key);
  if (key == "chassis") {
    chassis = getStep(key, value, step, 180, 0, type);
    CAR_ARM.ChassisCmd(chassis);
  } else if (key == "shoulder") {
    shoulder = getStep(key, value, 20, 180, 0, type);
    CAR_ARM.ShoulderCmd(shoulder);
  } else if (key == "elbow") {
    elbow = getStep(key, value, 10, 180, 0, type);
    CAR_ARM.ElbowCmd(elbow);
  } else if (key == "claws") {
    claws = getStep(key, value, step, 180, 90, type);
    CAR_ARM.ClawsCmd(claws);
  } else if (key == "wrist") {
    wrist = getStep(key, value, step, 180, 0, type);
    CAR_ARM.WristCmd(wrist);
  }
  String status = "True";
  ble.ClientSend(status);
};


void loop() {

  ble.ClientReceived();   // Received data

  if (!ble.connected) {
    CAR_ARM.Vehicle_Move(Stop, 0);    // stop
  }

  // Serial.println(ble.ClientData);

  // If connected and characteristic is writable, send data
  if (ble.connected && ble.pRemoteCharacteristic && ble.pRemoteCharacteristic->canWrite()) {
    delay(5);
    if (strcmp(ble.ClientData.c_str(), "command11") == 0) {
      CAR_ARM.Vehicle_Move(Forward, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command12") == 0) {
      CAR_ARM.Vehicle_Move(Backward, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command15") == 0) {
      CAR_ARM.Vehicle_Move(Move_Left, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command16") == 0) {
      CAR_ARM.Vehicle_Move(Move_Right, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command13") == 0) {
      CAR_ARM.Vehicle_Move(Contrarotate, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command14") == 0) {
      CAR_ARM.Vehicle_Move(Clockwise, 255);
    } else if (strcmp(ble.ClientData.c_str(), "command0") == 0) {
      CAR_ARM.Vehicle_Move(Stop, 0);
    } else if (strcmp(ble.ClientData.c_str(), "command3") == 0) {
      sendCommand("chassis", chassis, false);
    } else if (strcmp(ble.ClientData.c_str(), "command4") == 0) {
      sendCommand("chassis", chassis, true);
    } else if (strcmp(ble.ClientData.c_str(), "command1") == 0) {
      Serial.println("command1");
      sendCommand("shoulder", shoulder, false);
    } else if (strcmp(ble.ClientData.c_str(), "command2") == 0) {
      Serial.println("command2");
      sendCommand("shoulder", shoulder, true);
    } else if (strcmp(ble.ClientData.c_str(), "command5") == 0) {
      sendCommand("elbow", elbow, true);
    } else if (strcmp(ble.ClientData.c_str(), "command6") == 0) {
      sendCommand("elbow", elbow, false);
    } else if (strcmp(ble.ClientData.c_str(), "command7") == 0) {
      sendCommand("claws", claws, true);
    } else if (strcmp(ble.ClientData.c_str(), "command8") == 0) {
      sendCommand("claws", claws, false);
    } else if (strcmp(ble.ClientData.c_str(), "command9") == 0) {
      sendCommand("wrist", wrist, false);
    } else if (strcmp(ble.ClientData.c_str(), "command10") == 0) {
      sendCommand("wrist", wrist, true);
    }
    ble.ClientData = "";
  }
}