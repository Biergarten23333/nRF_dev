#include <Stepper.h>
#include <math.h>

#define INTERNAL_STEPS 32
#define GEAR_RATIO 64.0

// =========================
// Encoder pins
// =========================
const int PinCLK = 2;
const int PinDT  = 3;
const int PinSW  = 4;

// =========================
// 74HC595 pins
// =========================
const int dataPin  = 5;   // DS
const int latchPin = 6;   // STCP
const int clockPin = 7;   // SHCP

// =========================
// Stepper pins (28BYJ-48)
// In1, In3, In2, In4
// =========================
Stepper small_stepper(INTERNAL_STEPS, 8, 10, 9, 11);

// =========================
// 4-digit select pins
// =========================
const int digitPins[4] = {A1, A2, A3, A4};

// =========================
// Display type settings
// Based on your previous wiring to GND,
// this is most likely COMMON CATHODE:
// digit LOW = ON, segment HIGH = ON
// =========================
const bool COMMON_ANODE = false;   // if display shows inverted, change to true

// =========================
// Direction settings
// If encoder positive/negative is reversed, change ENCODER_SIGN to -1
// If motor CW/CCW is reversed, change MOTOR_SIGN to -1
// =========================
const int ENCODER_SIGN = 1;
const int MOTOR_SIGN   = -1;   // 你前面说电机方向反了，这里默认先反过来

// =========================
// RPM settings
// =========================
float setOutputRPM = 0.0f;      // value being set by encoder
float activeOutputRPM = 0.0f;   // value confirmed and used by motor
float displayRPM = 0.0f;        // value currently shown on display

const float rpmStep  = 0.1f;
const float rpmLimit = 18.0f;

bool motorRunning = false;

// =========================
// Encoder ISR flags
// =========================
volatile bool turnDetected = false;
volatile bool rotationDirection = false;

// =========================
// Button debounce
// =========================
bool lastButtonReading = HIGH;
bool stableButtonState = HIGH;
unsigned long lastDebounceTime = 0;
const unsigned long debounceDelay = 50;

// =========================
// 7-segment codes for 0-9
// bit0..bit7 = a b c d e f g dp
// =========================
const byte segDigits[10] = {
  0x3F, // 0
  0x06, // 1
  0x5B, // 2
  0x4F, // 3
  0x66, // 4
  0x6D, // 5
  0x7D, // 6
  0x07, // 7
  0x7F, // 8
  0x6F  // 9
};

const byte SEG_DASH  = 0x40; // g
const byte SEG_BLANK = 0x00;

// display buffer
byte displayBuf[4]  = {SEG_BLANK, SEG_BLANK, SEG_BLANK, SEG_BLANK};
bool displayDot[4]  = {false, false, false, false};

// =========================
// Helper: send one segment byte to 74HC595
// =========================
void sendSegmentRaw(byte seg, bool dot) {
  byte out = seg;
  if (dot) out |= 0x80;

  if (COMMON_ANODE) {
    out = ~out;
  }

  digitalWrite(latchPin, LOW);
  shiftOut(dataPin, clockPin, MSBFIRST, out);
  digitalWrite(latchPin, HIGH);
}

// =========================
// Helper: disable all digits
// =========================
void disableAllDigits() {
  for (int i = 0; i < 4; i++) {
    digitalWrite(digitPins[i], COMMON_ANODE ? LOW : HIGH);
  }
}

// =========================
// Helper: enable one digit
// =========================
void enableDigit(int idx) {
  digitalWrite(digitPins[idx], COMMON_ANODE ? HIGH : LOW);
}

// =========================
// Display refresh (dynamic scan)
// call as often as possible
// =========================
void refreshDisplay() {
  static int currentDigit = 0;
  static unsigned long lastMicros = 0;

  if (micros() - lastMicros < 1500) return;
  lastMicros = micros();

  disableAllDigits();
  sendSegmentRaw(displayBuf[currentDigit], displayDot[currentDigit]);
  enableDigit(currentDigit);

  currentDigit++;
  if (currentDigit >= 4) currentDigit = 0;
}

// =========================
// Convert RPM to 4-digit display
// format examples:
//   0.0  -> "0.0"
//   3.5  -> "3.5"
//  -3.5  -> "-3.5"
//  12.0  -> "12.0"
// -18.0  -> "-18.0"  (actually 5 chars, so display "-18.")
// For 4 digits we use:
//   3.5   => [3].[5][ ][ ]
//   12.0  => [1][2].[0][ ]
//   -3.5  => [-][3].[5][ ]
//   -18.0 => [-][1][8].[0]
// =========================
void setDisplayRPM(float rpm) {
  for (int i = 0; i < 4; i++) {
    displayBuf[i] = SEG_BLANK;
    displayDot[i] = false;
  }

  bool neg = (rpm < 0.0f);
  float absRpm = fabs(rpm);

  int value10 = (int)(absRpm * 10.0f + 0.5f); // keep 1 decimal
  int intPart = value10 / 10;
  int frac    = value10 % 10;

  if (!neg) {
    if (intPart < 10) {
      // 3.5
      displayBuf[0] = segDigits[intPart];
      displayDot[0] = true;
      displayBuf[1] = segDigits[frac];
    } else {
      // 12.0
      displayBuf[0] = segDigits[intPart / 10];
      displayBuf[1] = segDigits[intPart % 10];
      displayDot[1] = true;
      displayBuf[2] = segDigits[frac];
    }
  } else {
    if (intPart < 10) {
      // -3.5
      displayBuf[0] = SEG_DASH;
      displayBuf[1] = segDigits[intPart];
      displayDot[1] = true;
      displayBuf[2] = segDigits[frac];
    } else {
      // -18.0
      displayBuf[0] = SEG_DASH;
      displayBuf[1] = segDigits[intPart / 10];
      displayBuf[2] = segDigits[intPart % 10];
      displayDot[2] = true;
      displayBuf[3] = segDigits[frac];
    }
  }
}

// =========================
// Serial prints
// =========================
void printSetRPM() {
  Serial.print("Set output RPM: ");
  Serial.println(setOutputRPM, 1);
}

void printConfirmedRPM() {
  Serial.print("RPM ");
  Serial.print(activeOutputRPM, 1);
  Serial.println(" setted");
}

// =========================
// Release stepper coils
// =========================
void releaseMotor() {
  digitalWrite(8, LOW);
  digitalWrite(9, LOW);
  digitalWrite(10, LOW);
  digitalWrite(11, LOW);
}

// =========================
// Encoder interrupt
// =========================
void isr() {
  if (digitalRead(PinCLK)) {
    rotationDirection = digitalRead(PinDT);
  } else {
    rotationDirection = !digitalRead(PinDT);
  }
  turnDetected = true;
}

// =========================
// Setup
// =========================
void setup() {
  pinMode(PinCLK, INPUT);
  pinMode(PinDT, INPUT);
  pinMode(PinSW, INPUT_PULLUP);

  pinMode(dataPin, OUTPUT);
  pinMode(latchPin, OUTPUT);
  pinMode(clockPin, OUTPUT);

  for (int i = 0; i < 4; i++) {
    pinMode(digitPins[i], OUTPUT);
  }

  disableAllDigits();
  releaseMotor();

  Serial.begin(115200);
  attachInterrupt(digitalPinToInterrupt(PinCLK), isr, FALLING);

  displayRPM = 0.0f;
  setDisplayRPM(displayRPM);
  printSetRPM();
}

// =========================
// Loop
// =========================
void loop() {
  refreshDisplay();

  // ---------- Encoder rotates: only change set value ----------
  if (turnDetected) {
    noInterrupts();
    bool dir = rotationDirection;
    turnDetected = false;
    interrupts();

    if (dir) {
      setOutputRPM -= rpmStep * ENCODER_SIGN;
    } else {
      setOutputRPM += rpmStep * ENCODER_SIGN;
    }

    if (setOutputRPM > rpmLimit)  setOutputRPM = rpmLimit;
    if (setOutputRPM < -rpmLimit) setOutputRPM = -rpmLimit;

    displayRPM = setOutputRPM;
    setDisplayRPM(displayRPM);
    printSetRPM();
  }

  // ---------- Button confirm ----------
  bool reading = digitalRead(PinSW);

  if (reading != lastButtonReading) {
    lastDebounceTime = millis();
  }

  if ((millis() - lastDebounceTime) > debounceDelay) {
    if (reading != stableButtonState) {
      stableButtonState = reading;

      if (stableButtonState == LOW) {
        activeOutputRPM = setOutputRPM;
        motorRunning = true;

        displayRPM = activeOutputRPM;
        setDisplayRPM(displayRPM);

        printConfirmedRPM();

        if (activeOutputRPM == 0.0f) {
          Serial.println("Motor stopped");
          releaseMotor();
        }
      }
    }
  }

  lastButtonReading = reading;

  // ---------- Run motor ----------
  if (motorRunning && activeOutputRPM != 0.0f) {
    float internalRPM = fabs(activeOutputRPM) * GEAR_RATIO;
    small_stepper.setSpeed((long)(internalRPM + 0.5f));

    int stepDir = (activeOutputRPM > 0.0f) ? 1 : -1;
    stepDir *= MOTOR_SIGN;

    small_stepper.step(stepDir);
  } else {
    releaseMotor();
  }

  // keep display alive during blocking loop
  refreshDisplay();
}