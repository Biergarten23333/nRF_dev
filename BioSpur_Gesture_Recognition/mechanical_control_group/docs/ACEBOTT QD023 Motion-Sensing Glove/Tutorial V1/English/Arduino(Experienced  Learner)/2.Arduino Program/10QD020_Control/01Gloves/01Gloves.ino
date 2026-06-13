#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <ACB_Gloves.h>
#include "esp_task_wdt.h"

ACB_Gloves gloves;     // gloves object

volatile bool linkOK = false;                
volatile unsigned long lastSendTime = 0;     
const unsigned long linkTimeout = 2000;      

unsigned long lastBlinkTime = 0;
const unsigned long blinkPeriod = 300;       
bool ledState = false;

uint8_t peerMac[] = {0xC4, 0xD8, 0xD5, 0x02, 0x28, 0xA3};

typedef struct struct_message {
  int id;
  int counter;
  bool button;
} struct_message;

struct_message myData;

// Enumerations define a type called Command that contains a set of constant values to represent different commands
enum Command {
  STOP = 0,
  FORWARD,
  BACKWARD,
  MOVE_LEFT,
  MOVE_RIGHT,

  Lie,
  Standby,

  Sleep,
  Dance1,
  Dance2,
  Dance3,

  Hello,
  Pushup,
  Fighting

};

Command lastCommand = STOP;  // Initial stop

void onSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  if (status == ESP_NOW_SEND_SUCCESS) {
    linkOK = true;
    lastSendTime = millis();
    Serial.println("ESP-NOW Send: Success");
  } else {
    linkOK = false;
    Serial.println("ESP-NOW Send: Fail");
  }
}

void setup() {
  Serial.begin(115200);

  gloves.init();
  
  pinMode(17, OUTPUT);  // Led init

  WiFi.mode(WIFI_STA);

  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(5, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW Init failed");
    return;
  }

  esp_now_register_send_cb(onSent);

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, peerMac, 6);
  peerInfo.channel = 5;  
  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("Add Peer Failed");
    return;
  }

  esp_task_wdt_init(10000, true);
  xTaskCreatePinnedToCore(loopMotorTask1, "MotorTask", 20000, NULL, 5, NULL, 0);    // Multithreading data acquisition

  Serial.println("ESP32 Ready, sending...");
}

// Loop function to update the characteristic value and send notifications
unsigned long previousBlinkMillis = 0;  // Store the time for LED blinking
const long blinkInterval = 500;         // Set the blink interval (500 milliseconds)
String cmd;

const char* Sprder_state = "command0";

void loop() {


    // -------- LED connection status control (place at the beginning of loop) --------
    unsigned long now = millis();

    // If no successful transmission occurs for a long time, treat it as disconnected
    if (now - lastSendTime > linkTimeout) {
      linkOK = false;
    }

    // Connected: LED stays ON (assuming LOW means ON, change to HIGH if needed)
    if (linkOK) {
      digitalWrite(17, LOW);   // Solid ON
    }
    // Not connected: LED blinks
    else {
      if (now - lastBlinkTime >= blinkPeriod) {
        lastBlinkTime = now;
        ledState = !ledState;
        digitalWrite(17, ledState ? LOW : HIGH);  // Blinking
      }
    }


    Command currentCommand = STOP;

    cmd = gloves.get_command();

    // Serial.println(cmd);

   // ---------------------------- car move ----------------------------
    if (cmd == "command11") {  // FORWARD
      currentCommand = FORWARD;
    }

    else if (cmd == "command12") {  // BACKWARD
      currentCommand = BACKWARD;
    }

    else if (cmd == "command15") {  // Move_Left
      currentCommand = MOVE_LEFT;
    }

    else if (cmd == "command16") {  // Move_Right
      currentCommand = MOVE_RIGHT;
    }

    else if (cmd == "command2") {  // lie
      currentCommand = Lie;
    }

    else if (cmd == "command1") {  // standby
      currentCommand = Standby;
    }

    else if (cmd == "command6") {  // sleep
      currentCommand = Sleep;
    }

    else if (cmd == "command5") {  // Dance1
      currentCommand = Dance1;
    }

    else if (cmd == "command10") {  // Dance2
      currentCommand = Dance2;
    }

    else if (cmd == "command9") {  // Dance3
      currentCommand = Dance3;
    }

    else if (cmd == "command8") {  // hello
      currentCommand = Hello;
    }

    else if (cmd == "command13") {  // pushup
      currentCommand = Pushup;
    }

    else if (cmd == "command14") {  // Fighting
      currentCommand = Fighting;
    }

    else {
      currentCommand = STOP;
    }
  // Serial.println(currentCommand);
    // Send data only when the current command is different from the last command sent
  if (currentCommand != lastCommand) {

      switch (currentCommand) {
          case FORWARD:
            Sprder_state = "command11";  
            break;
          case BACKWARD:
            Sprder_state = "command12";  
            break;
          case MOVE_LEFT:
            Sprder_state = "command15";  
            break;
          case MOVE_RIGHT:
            Sprder_state = "command16";  
            break;

          case Lie:
            Sprder_state = "command2";  
            break;
          case Standby:
            Sprder_state = "command1";  
            break;           

          case Sleep:
            Sprder_state = "command6";  
            break;
          case Dance1:
            Sprder_state = "command5";  
            break;
          case Dance2:
            Sprder_state = "command10";  
            break;
          case Dance3:
            Sprder_state = "command9";  
            break;

          case Hello:
            Sprder_state = "command8";  
            break;
          case Pushup:
            Sprder_state = "command13";  
            break;
          case Fighting:
            Sprder_state = "command14";  
            break;

          case STOP:
            Sprder_state = "command0";
            break;
  
      }
    Serial.println(Sprder_state);
    esp_err_t result = esp_now_send(peerMac, (uint8_t*)Sprder_state, strlen(Sprder_state));  // 发送Sprder_state长度

    delay(200);

    lastCommand = currentCommand;
  }
}
// Multithreading data acquisition
void loopMotorTask1(void* pvParameters) {
  for (;;) {
     gloves.get_data();
    esp_task_wdt_reset();
  }
}

