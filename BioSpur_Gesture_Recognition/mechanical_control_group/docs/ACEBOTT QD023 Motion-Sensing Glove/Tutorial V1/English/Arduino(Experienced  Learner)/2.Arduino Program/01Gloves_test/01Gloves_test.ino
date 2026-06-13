#include <ACB_Gloves.h>

ACB_Gloves gloves;     // gloves object

// Setup function to initialize the server and characteristics
void setup() {
  Serial.begin(115200);  // Start the Serial communication for debugging
  gloves.init();
}


void loop() {
  gloves.get_data();    // GET DATA
  Serial.println(gloves.get_command());   // GET COMMAND
}