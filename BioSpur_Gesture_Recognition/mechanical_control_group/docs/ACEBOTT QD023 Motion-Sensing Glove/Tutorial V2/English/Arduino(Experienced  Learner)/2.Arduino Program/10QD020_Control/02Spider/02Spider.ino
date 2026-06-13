#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <user_interface.h>
#include "text.h"
#include <espnow.h>

#define CMD_BUF_SIZE 32            
char receivedCmd[CMD_BUF_SIZE];    
bool newCmd = false;               

String sendBuff;//Send buffer
byte RX_package[17] = {0};//An array of incoming packets
byte callback_forward_package[5] = {0xff,0x55,0x02,0x01,0x01};// Forward command packet
byte callback_back_package[5] = {0xff,0x55,0x02,0x01,0x02};// Back command packet
byte callback_leftmove_package[5] = {0xff,0x55,0x02,0x01,0x03};// Move command packet left
byte callback_rightmove_package[5] = {0xff,0x55,0x02,0x01,0x04};// Right shift command packet
byte callback_turnleft_package[5] = {0xff,0x55,0x02,0x01,0x05};// Left turn command packet
byte callback_turnright_package[5] = {0xff,0x55,0x02,0x01,0x06};// Right turn command packet
byte callback_standby_package[5] = {0xff,0x55,0x02,0x01,0x07};// standby command packet
byte callback_sleep_package[5] = {0xff,0x55,0x02,0x01,0x08};//  command packet
byte callback_lie_package[5] = {0xff,0x55,0x02,0x01,0x09};// command packet
byte callback_hello_package[5] = {0xff,0x55,0x02,0x01,0x0a};// command packet
byte callback_pushup_package[5] = {0xff,0x55,0x02,0x01,0x0b};// command packet
byte callback_fighting_package[5] = {0xff,0x55,0x02,0x01,0x0c};// command packet
byte callback_dance1_package[5] = {0xff,0x55,0x02,0x01,0x0d};// command packet
byte callback_dance2_package[5] = {0xff,0x55,0x02,0x01,0x0e};// command packet
byte callback_dance3_package[5] = {0xff,0x55,0x02,0x01,0x0f};// command packet

void setup()
{
  Serial.setTimeout(10); // Set the serial timeout to 10ms
  Serial.begin(115200);  // Initiate serial communication with baud rate of 115200

  delay(1000);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);  
  wifi_set_channel(5);  
  delay(200); 

  Serial.print("WiFi Channel: ");
  Serial.println(WiFi.channel());

  if (esp_now_init() != 0) {
    Serial.println("ESP-NOW init failed!");
    return;
  }

  servo_14.attach(14, SERVOMIN, SERVOMAX);// Connect the servo to pin 14
  servo_12.attach(12, SERVOMIN, SERVOMAX);// Connect the servo to pin 12
  servo_13.attach(13, SERVOMIN, SERVOMAX);// Connect the servo to pin 13
  servo_15.attach(15, SERVOMIN, SERVOMAX);// Connect the servo to pin 15
  servo_16.attach(16, SERVOMIN, SERVOMAX);// Connect the servo to pin 16
  servo_5.attach(5, SERVOMIN, SERVOMAX);// Connect the servo to pin 5
  servo_4.attach(4, SERVOMIN, SERVOMAX);// Connect the servo to pin 4
  servo_2.attach(2, SERVOMIN, SERVOMAX);// Connect the servo to pin 2

  Servo_PROGRAM_Zero();// Reset the servo program to zero

  esp_now_set_self_role(ESP_NOW_ROLE_SLAVE);
  esp_now_register_recv_cb(onDataRecv);

  Serial.println("ESP8266 Ready, waiting for ESP32...");
  
  delay(1000);
  Serial.println();
  Serial.print("ESP8266 MAC: ");
  Serial.println(WiFi.macAddress());
}

void onDataRecv(uint8_t *mac, uint8_t *data, uint8_t len) {

  if (len >= CMD_BUF_SIZE) len = CMD_BUF_SIZE - 1; 

  memcpy(receivedCmd, data, len);
  receivedCmd[len] = '\0';   

  newCmd = true;

  Serial.print("Received: ");
  Serial.println(receivedCmd);

}

void loop() {

  if (strcmp(receivedCmd, "command11") == 0) {
    forward();
  } 
  else if (strcmp(receivedCmd, "command12") == 0) {
    back();
  } 
  else if (strcmp(receivedCmd, "command15") == 0) {
    turnleft();
  } 
  else if (strcmp(receivedCmd, "command16") == 0) {
    turnright();
  } 

  if (newCmd) {
    newCmd = false;  
    Serial.println(receivedCmd);


    if (strcmp(receivedCmd, "command2") == 0) {
      lie();
    } 
    else if (strcmp(receivedCmd, "command1") == 0) {
      standby();
    }

    else if (strcmp(receivedCmd, "command6") == 0) {
      sleep();
    } 
    else if (strcmp(receivedCmd, "command5") == 0) {
      dance2();
    } 
    else if (strcmp(receivedCmd, "command10") == 0) {
      dance1();
    } 
    else if (strcmp(receivedCmd, "command9") == 0) {
      dance3();
    }

    else if (strcmp(receivedCmd, "command8") == 0) {
      hello();
    } 
    else if (strcmp(receivedCmd, "command13") == 0) {
      pushup();
    }
    else if (strcmp(receivedCmd, "command14") == 0) {
      fighting();
    }

    else if (strcmp(receivedCmd, "command0") == 0) {
    }
  }
}