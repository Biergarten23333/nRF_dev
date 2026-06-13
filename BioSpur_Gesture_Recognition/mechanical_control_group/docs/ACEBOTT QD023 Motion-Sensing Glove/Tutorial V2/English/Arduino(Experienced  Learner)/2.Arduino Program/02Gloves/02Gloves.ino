#include <ACB_Gloves.h>
#include <ACB_BLEDevice.h>
#include "soc/rtc_wdt.h"
#include "esp_task_wdt.h"

#define SERVICE_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"         // Unique identifier for the service
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"  // Unique identifier for the characteristic

ACB_BLEDevice ble(SERVICE_UUID,CHARACTERISTIC_UUID);    // Ble object
ACB_Gloves gloves;     // gloves object

const char* car_state;         // Car move state

// Enumerations define a type called Command that contains a set of constant values to represent different commands
enum Command {
  STOP = 0,
  FORWARD,
  BACKWARD,
  CONTRAROTATE,
  CLOCKWISE,
  MOVE_LEFT,
  MOVE_RIGHT,
  SHOOT
};

Command lastCommand = STOP;  // Initial stop

// Setup function to initialize the server and characteristics
void setup() {
  Serial.begin(115200);  // Start the Serial communication for debugging

  ble.Bluetooth_Name("ESP32-BLE");  // BLE init

  gloves.init();

  pinMode(17, OUTPUT);  // Led init

  esp_task_wdt_init(10000, true);
  xTaskCreatePinnedToCore(loopMotorTask1, "MotorTask", 20000, NULL, 5, NULL, 0);    // Multithreading data acquisition
}

// Loop function to update the characteristic value and send notifications
unsigned long previousBlinkMillis = 0;  // Store the time for LED blinking
const long blinkInterval = 500;         // Set the blink interval (500 milliseconds)

void loop() {

  // If a device is connected and the interval time has passed
  if (ble.deviceConnected) {
    digitalWrite(17, LOW);  // LED 17 open

    Command currentCommand = STOP;

    // ---------------------------- car move ----------------------------
    if (gloves.get_command() == "command11") {  // FORWARD
      currentCommand = FORWARD;
    }

    else if (gloves.get_command() == "command12") {  // BACKWARD
      currentCommand = BACKWARD;
    }

    else if (gloves.get_command() == "command15") {  // Move_Left
      currentCommand = MOVE_LEFT;
    }

    else if (gloves.get_command() == "command16") {  // Move_Right
      currentCommand = MOVE_RIGHT;
    }

    else if (gloves.get_command() == "command13") {  // CONTRAROTATE
      currentCommand = CONTRAROTATE;
    }

    else if (gloves.get_command() == "command14") {  // CLOCKWISE
      currentCommand = CLOCKWISE;
    }

    else if (gloves.get_command() == "command17") {  // SHOOT
      currentCommand = SHOOT;
    }

    else if (gloves.get_command() == "command3") {
      //chassis add
      sendCommand("command3");


    } else if (gloves.get_command() == "command4") {
      //chassis lower
      sendCommand("command4");
    }


    else if (gloves.get_command() == "command1") {
      //shoulder lower
      sendCommand("command1");
      
    } else if (gloves.get_command() == "command2") {
      //shoulder add
      sendCommand("command2");
      
    }

    else if (gloves.get_command() == "command5") {
      //elbow add
      sendCommand("command5");
    } else if (gloves.get_command() == "command6") {
      //elbow lower
      sendCommand("command6");
    }

    else if (gloves.get_command() == "command9") {
      //wrist add
      sendCommand("command9");
    } else if (gloves.get_command() == "command10") {
      //wrist lower
      sendCommand("command10");
    }

    else if (gloves.get_command() == "command7") {
      //claws add
      sendCommand("command7");
    } else if (gloves.get_command() == "command8") {
      //claws lower
      sendCommand("command8");
    }

    else {
      currentCommand = STOP;
    }

    // Send data only when the current command is different from the last command sent
    if (currentCommand != lastCommand) {
      ble.last = "True";
      switch (currentCommand) {
        case FORWARD:
          car_state = "command11";  // forward
          break;
        case BACKWARD:
          car_state = "command12";  // backward
          break;
        case CONTRAROTATE:
          car_state = "command13";  // contrarotate
          break;
        case CLOCKWISE:
          car_state = "command14";  // clockwise
          break;
        case MOVE_LEFT:
          car_state = "command15";  // move_left
          break;
        case MOVE_RIGHT:
          car_state = "command16";  // move_right
          break;
        case SHOOT:
          car_state = "command17";  // move_right
          break;
        case STOP:
          car_state = "command0";
          break;
        // default:
        //   car_state = "command0";
        //   break;
      }
      ble.ServerSend(car_state);
      lastCommand = currentCommand;

      // Update last command
    }

    // ---------------------------- car move ----------------------------
  } else {
    unsigned long currentMillis = millis();
    if (currentMillis - previousBlinkMillis >= blinkInterval) {
      previousBlinkMillis = currentMillis;  // Reset the blink timer
      int ledState = digitalRead(17);       // Read the current state of LED 17
      digitalWrite(17, !ledState);          // Toggle the LED state
    }
  }
}

void sendCommand(String key) {
  // Serial.println(ble.last.c_str());
  if (ble.last == "True") {
    ble.ServerSend(key);
    ble.last = "False";
  }
}

// Multithreading data acquisition
void loopMotorTask1(void* pvParameters) {
  for (;;) {
    getData();
    esp_task_wdt_reset();
  }
}

void getData() {
  ble.ServerReceived();
  if (ble.ServerData.length() > 0){
    ble.last = ble.ServerData.c_str();
    if (ble.last == "command0"){
      ble.last = "True";
    }
  }
  gloves.get_data();
}