#include "anchor_ultrasound.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#ifndef APP_ANCHOR_ULTRASOUND_ENABLE
#define APP_ANCHOR_ULTRASOUND_ENABLE 0U
#endif

#ifndef APP_ANCHOR_ULTRASOUND_TRIG_PIN
#define APP_ANCHOR_ULTRASOUND_TRIG_PIN 6U
#endif

#ifndef APP_ANCHOR_ULTRASOUND_ECHO_PIN
#define APP_ANCHOR_ULTRASOUND_ECHO_PIN 7U
#endif

#ifndef APP_ANCHOR_ULTRASOUND_ECHO_TIMEOUT_US
#define APP_ANCHOR_ULTRASOUND_ECHO_TIMEOUT_US 30000U
#endif

#ifndef APP_ANCHOR_ULTRASOUND_SAMPLE_PERIOD_MS
#define APP_ANCHOR_ULTRASOUND_SAMPLE_PERIOD_MS 100U
#endif

#ifndef APP_ANCHOR_ULTRASOUND_MAX_SAMPLES
#define APP_ANCHOR_ULTRASOUND_MAX_SAMPLES 300U
#endif

#define US_MAX_DURATION_S 120U

#if APP_ANCHOR_ULTRASOUND_ENABLE

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>

static const struct device *g_gpio0;
static struct k_mutex g_lock;
static struct k_work_delayable g_work;

static bool g_initialized;
static bool g_running;
static bool g_stop_requested;
static int g_init_rc;
static uint32_t g_target_samples;
static uint32_t g_attempts;
static uint32_t g_ok;
static uint32_t g_timeouts;
static uint32_t g_latest_mm;
static uint32_t g_latest_echo_us;
static uint32_t g_min_mm;
static uint32_t g_max_mm;
static uint64_t g_sum_mm;
static uint16_t g_samples[APP_ANCHOR_ULTRASOUND_MAX_SAMPLES];

static uint32_t cycles_to_us(uint32_t cycles)
{
    return (uint32_t)k_cyc_to_us_floor64(cycles);
}

static int wait_for_echo_level(int level, uint32_t timeout_us)
{
    const uint32_t start = k_cycle_get_32();
    const uint32_t timeout_cycles = k_us_to_cyc_floor32(timeout_us);

    while ((uint32_t)(k_cycle_get_32() - start) < timeout_cycles) {
        int value = gpio_pin_get(g_gpio0, APP_ANCHOR_ULTRASOUND_ECHO_PIN);

        if (value < 0) {
            return value;
        }
        if (value == level) {
            return 0;
        }
    }

    return -ETIMEDOUT;
}

static int measure_echo_us(uint32_t *echo_us)
{
    int rc;
    uint32_t rise;
    uint32_t fall;

    rc = gpio_pin_set(g_gpio0, APP_ANCHOR_ULTRASOUND_TRIG_PIN, 0);
    if (rc != 0) {
        return rc;
    }
    k_busy_wait(3);
    rc = gpio_pin_set(g_gpio0, APP_ANCHOR_ULTRASOUND_TRIG_PIN, 1);
    if (rc != 0) {
        return rc;
    }
    k_busy_wait(10);
    rc = gpio_pin_set(g_gpio0, APP_ANCHOR_ULTRASOUND_TRIG_PIN, 0);
    if (rc != 0) {
        return rc;
    }

    rc = wait_for_echo_level(1, APP_ANCHOR_ULTRASOUND_ECHO_TIMEOUT_US);
    if (rc != 0) {
        return rc;
    }
    rise = k_cycle_get_32();

    rc = wait_for_echo_level(0, APP_ANCHOR_ULTRASOUND_ECHO_TIMEOUT_US);
    if (rc != 0) {
        return rc;
    }
    fall = k_cycle_get_32();

    *echo_us = cycles_to_us(fall - rise);
    return 0;
}

static uint32_t echo_us_to_distance_mm(uint32_t echo_us)
{
    return (echo_us * 343U + 1000U) / 2000U;
}

static uint32_t median_mm_locked(void)
{
    uint16_t tmp[APP_ANCHOR_ULTRASOUND_MAX_SAMPLES];
    uint32_t n = g_ok;
    uint32_t i;

    if (n == 0U) {
        return 0U;
    }
    if (n > APP_ANCHOR_ULTRASOUND_MAX_SAMPLES) {
        n = APP_ANCHOR_ULTRASOUND_MAX_SAMPLES;
    }

    memcpy(tmp, g_samples, n * sizeof(tmp[0]));
    for (i = 1U; i < n; i++) {
        uint16_t v = tmp[i];
        uint32_t j = i;

        while (j > 0U && tmp[j - 1U] > v) {
            tmp[j] = tmp[j - 1U];
            j--;
        }
        tmp[j] = v;
    }

    if ((n & 1U) == 0U) {
        return ((uint32_t)tmp[(n / 2U) - 1U] + (uint32_t)tmp[n / 2U]) / 2U;
    }
    return (uint32_t)tmp[n / 2U];
}

static void reset_stats_locked(uint32_t target_samples)
{
    g_target_samples = target_samples;
    g_attempts = 0U;
    g_ok = 0U;
    g_timeouts = 0U;
    g_latest_mm = 0U;
    g_latest_echo_us = 0U;
    g_min_mm = 0U;
    g_max_mm = 0U;
    g_sum_mm = 0U;
    memset(g_samples, 0, sizeof(g_samples));
}

static void record_sample_locked(uint32_t echo_us, uint32_t distance_mm)
{
    g_latest_echo_us = echo_us;
    g_latest_mm = distance_mm;
    if (g_ok == 0U || distance_mm < g_min_mm) {
        g_min_mm = distance_mm;
    }
    if (g_ok == 0U || distance_mm > g_max_mm) {
        g_max_mm = distance_mm;
    }
    if (g_ok < APP_ANCHOR_ULTRASOUND_MAX_SAMPLES) {
        g_samples[g_ok] = (uint16_t)distance_mm;
    }
    g_ok++;
    g_sum_mm += distance_mm;
}

static void finish_locked(void)
{
    g_running = false;
    g_stop_requested = false;
    (void)gpio_pin_set(g_gpio0, APP_ANCHOR_ULTRASOUND_TRIG_PIN, 0);
}

static void ultrasound_work_handler(struct k_work *work)
{
    bool should_run;

    ARG_UNUSED(work);

    k_mutex_lock(&g_lock, K_FOREVER);
    should_run = g_running && !g_stop_requested && g_attempts < g_target_samples;
    if (!should_run) {
        finish_locked();
        k_mutex_unlock(&g_lock);
        return;
    }
    k_mutex_unlock(&g_lock);

    uint32_t echo_us = 0U;
    int rc = measure_echo_us(&echo_us);
    uint32_t distance_mm = echo_us_to_distance_mm(echo_us);

    k_mutex_lock(&g_lock, K_FOREVER);
    if (g_running && !g_stop_requested) {
        g_attempts++;
        if (rc == 0) {
            record_sample_locked(echo_us, distance_mm);
        } else {
            g_timeouts++;
        }
    }

    should_run = g_running && !g_stop_requested && g_attempts < g_target_samples;
    if (!should_run) {
        finish_locked();
        k_mutex_unlock(&g_lock);
        return;
    }

    k_mutex_unlock(&g_lock);
    (void)k_work_schedule(&g_work, K_MSEC(APP_ANCHOR_ULTRASOUND_SAMPLE_PERIOD_MS));
}

int anchor_ultrasound_init(void)
{
    int rc;

    if (g_initialized) {
        return g_init_rc;
    }

    k_mutex_init(&g_lock);
    k_work_init_delayable(&g_work, ultrasound_work_handler);
    g_gpio0 = DEVICE_DT_GET(DT_NODELABEL(gpio0));
    if (!device_is_ready(g_gpio0)) {
        g_init_rc = -ENODEV;
        g_initialized = true;
        return g_init_rc;
    }

    rc = gpio_pin_configure(g_gpio0, APP_ANCHOR_ULTRASOUND_TRIG_PIN, GPIO_OUTPUT_INACTIVE);
    if (rc != 0) {
        g_init_rc = rc;
        g_initialized = true;
        return g_init_rc;
    }

    rc = gpio_pin_configure(g_gpio0, APP_ANCHOR_ULTRASOUND_ECHO_PIN, GPIO_INPUT);
    if (rc != 0) {
        (void)gpio_pin_configure(g_gpio0, APP_ANCHOR_ULTRASOUND_TRIG_PIN, GPIO_DISCONNECTED);
        g_init_rc = rc;
        g_initialized = true;
        return g_init_rc;
    }

    g_init_rc = 0;
    g_initialized = true;
    printk("anchor ultrasound ready trig=P0.%02u echo=P0.%02u period_ms=%u max_samples=%u\n",
           (unsigned int)APP_ANCHOR_ULTRASOUND_TRIG_PIN,
           (unsigned int)APP_ANCHOR_ULTRASOUND_ECHO_PIN,
           (unsigned int)APP_ANCHOR_ULTRASOUND_SAMPLE_PERIOD_MS,
           (unsigned int)APP_ANCHOR_ULTRASOUND_MAX_SAMPLES);
    return 0;
}

int anchor_ultrasound_start(uint32_t duration_s)
{
    uint32_t target_samples;
    int rc = anchor_ultrasound_init();

    if (rc != 0) {
        return rc;
    }
    if (duration_s == 0U) {
        duration_s = 30U;
    }
    if (duration_s > US_MAX_DURATION_S) {
        duration_s = US_MAX_DURATION_S;
    }

    target_samples = (duration_s * 1000U) / APP_ANCHOR_ULTRASOUND_SAMPLE_PERIOD_MS;
    if (target_samples == 0U) {
        target_samples = 1U;
    }
    if (target_samples > APP_ANCHOR_ULTRASOUND_MAX_SAMPLES) {
        target_samples = APP_ANCHOR_ULTRASOUND_MAX_SAMPLES;
    }

    k_mutex_lock(&g_lock, K_FOREVER);
    if (g_running) {
        k_mutex_unlock(&g_lock);
        return -EBUSY;
    }

    reset_stats_locked(target_samples);
    g_running = true;
    g_stop_requested = false;
    k_mutex_unlock(&g_lock);

    (void)k_work_schedule(&g_work, K_NO_WAIT);
    return 0;
}

void anchor_ultrasound_stop(void)
{
    if (!g_initialized || g_init_rc != 0) {
        return;
    }

    k_mutex_lock(&g_lock, K_FOREVER);
    g_stop_requested = true;
    (void)gpio_pin_set(g_gpio0, APP_ANCHOR_ULTRASOUND_TRIG_PIN, 0);
    k_mutex_unlock(&g_lock);
    (void)k_work_cancel_delayable(&g_work);

    k_mutex_lock(&g_lock, K_FOREVER);
    finish_locked();
    k_mutex_unlock(&g_lock);
}

bool anchor_ultrasound_busy(void)
{
    bool running;

    if (!g_initialized || g_init_rc != 0) {
        return false;
    }
    k_mutex_lock(&g_lock, K_FOREVER);
    running = g_running;
    k_mutex_unlock(&g_lock);
    return running;
}

void anchor_ultrasound_status(char *buf, size_t len)
{
    uint32_t mean_mm;
    uint32_t median_mm;
    bool running;

    if (buf == NULL || len == 0U) {
        return;
    }

    if (!g_initialized) {
        snprintf(buf, len,
                 "US;IDLE;enabled=1;trig=P0.%02u;echo=P0.%02u;period_ms=%u",
                 (unsigned int)APP_ANCHOR_ULTRASOUND_TRIG_PIN,
                 (unsigned int)APP_ANCHOR_ULTRASOUND_ECHO_PIN,
                 (unsigned int)APP_ANCHOR_ULTRASOUND_SAMPLE_PERIOD_MS);
        return;
    }

    if (g_init_rc != 0) {
        snprintf(buf, len, "US;ERR;init_rc=%d", g_init_rc);
        return;
    }

    k_mutex_lock(&g_lock, K_FOREVER);
    running = g_running;
    mean_mm = (g_ok == 0U) ? 0U : (uint32_t)(g_sum_mm / g_ok);
    median_mm = median_mm_locked();
    snprintf(buf, len,
             "US;%s;attempts=%lu;target=%lu;ok=%lu;timeout=%lu;latest_mm=%lu;median_mm=%lu;mean_mm=%lu;echo_us=%lu",
             running ? "RUNNING" : "DONE",
             (unsigned long)g_attempts,
             (unsigned long)g_target_samples,
             (unsigned long)g_ok,
             (unsigned long)g_timeouts,
             (unsigned long)g_latest_mm,
             (unsigned long)median_mm,
             (unsigned long)mean_mm,
             (unsigned long)g_latest_echo_us);
    k_mutex_unlock(&g_lock);
}

#else

int anchor_ultrasound_init(void)
{
    return -ENOTSUP;
}

int anchor_ultrasound_start(uint32_t duration_s)
{
    ARG_UNUSED(duration_s);
    return -ENOTSUP;
}

void anchor_ultrasound_stop(void)
{
}

bool anchor_ultrasound_busy(void)
{
    return false;
}

void anchor_ultrasound_status(char *buf, size_t len)
{
    if (buf == NULL || len == 0U) {
        return;
    }
    snprintf(buf, len, "US;DISABLED");
}

#endif
