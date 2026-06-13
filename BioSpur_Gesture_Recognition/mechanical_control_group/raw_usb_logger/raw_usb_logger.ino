#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

// ACEBOTT QD023 glove potentiometer pins from ACB_Gloves.h.
// The inferred finger order is thumb, index, middle, ring, pinky.
static constexpr int POT_THUMB_PIN = 36;
static constexpr int POT_INDEX_PIN = 39;
static constexpr int POT_MIDDLE_PIN = 34;
static constexpr int POT_RING_PIN = 35;
static constexpr int POT_PINKY_PIN = 32;

static constexpr uint32_t BAUD_RATE = 230400;
static constexpr uint32_t SAMPLE_PERIOD_US = 10000;  // 100 Hz

Adafruit_MPU6050 mpu;
uint32_t nextSampleUs = 0;

void setup() {
  Serial.begin(BAUD_RATE);
  delay(1000);

  analogReadResolution(12);       // ESP32 ADC output range: 0..4095
  analogSetAttenuation(ADC_11db); // Wider input range, suitable for 3.3 V sensors.

  Wire.begin();
  if (!mpu.begin()) {
    Serial.println("error,mpu6050_not_found");
    while (true) {
      delay(1000);
    }
  }

  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);

  Serial.println("t_us,thumb_adc,index_adc,middle_adc,ring_adc,pinky_adc,ax_ms2,ay_ms2,az_ms2,gx_rads,gy_rads,gz_rads,temp_c");
  nextSampleUs = micros();
}

void loop() {
  const uint32_t now = micros();
  if ((int32_t)(now - nextSampleUs) < 0) {
    return;
  }
  nextSampleUs += SAMPLE_PERIOD_US;

  sensors_event_t accel;
  sensors_event_t gyro;
  sensors_event_t temp;
  mpu.getEvent(&accel, &gyro, &temp);

  const int thumb = analogRead(POT_THUMB_PIN);
  const int index = analogRead(POT_INDEX_PIN);
  const int middle = analogRead(POT_MIDDLE_PIN);
  const int ring = analogRead(POT_RING_PIN);
  const int pinky = analogRead(POT_PINKY_PIN);

  Serial.print(now);
  Serial.print(',');
  Serial.print(thumb);
  Serial.print(',');
  Serial.print(index);
  Serial.print(',');
  Serial.print(middle);
  Serial.print(',');
  Serial.print(ring);
  Serial.print(',');
  Serial.print(pinky);
  Serial.print(',');
  Serial.print(accel.acceleration.x, 4);
  Serial.print(',');
  Serial.print(accel.acceleration.y, 4);
  Serial.print(',');
  Serial.print(accel.acceleration.z, 4);
  Serial.print(',');
  Serial.print(gyro.gyro.x, 5);
  Serial.print(',');
  Serial.print(gyro.gyro.y, 5);
  Serial.print(',');
  Serial.print(gyro.gyro.z, 5);
  Serial.print(',');
  Serial.println(temp.temperature, 2);
}
