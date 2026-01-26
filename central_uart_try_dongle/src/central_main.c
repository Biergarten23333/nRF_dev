#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/pwm.h>
#include <zephyr/sys/reboot.h>

#include "cdc_async.h"
#include "app_ble.h"
#include "app_cmd.h"


#define GPIO0_NODE DT_NODELABEL(gpio0)
#define GPIO1_NODE DT_NODELABEL(gpio1)

#define LD1_PIN       6   /* P0.06 */
#define LD2_R_PIN     8   /* P0.08 */
#define LD2_G_PIN     9   /* P1.09 */
#define LD2_B_PIN     12  /* P0.12 */

static const struct device *gpio0;
static const struct device *gpio1;


static const struct pwm_dt_spec ld1_pwm = PWM_DT_SPEC_GET(DT_ALIAS(pwm_led0));
static bool ld1_pwm_ready;

static bool ui_armed;
static app_ui_state_t ui_state = APP_UI_STATE_IDLE;

static int leds_init(void)
{
    gpio0 = DEVICE_DT_GET(GPIO0_NODE);
    gpio1 = DEVICE_DT_GET(GPIO1_NODE);
    if (!device_is_ready(gpio0) || !device_is_ready(gpio1)) {
        return -ENODEV;
    }

    if (!device_is_ready(ld1_pwm.dev)) {
        ld1_pwm_ready = false;
    } else {
        ld1_pwm_ready = true;
        (void)pwm_set_dt(&ld1_pwm, ld1_pwm.period, 0);
    }

    int err = 0;
    err |= gpio_pin_configure(gpio0, LD2_R_PIN, GPIO_OUTPUT_HIGH);
    err |= gpio_pin_configure(gpio1, LD2_G_PIN, GPIO_OUTPUT_HIGH);
    err |= gpio_pin_configure(gpio0, LD2_B_PIN, GPIO_OUTPUT_HIGH);
    if (!ld1_pwm_ready) {
        err |= gpio_pin_configure(gpio0, LD1_PIN, GPIO_OUTPUT_HIGH);
    }
    return err;
}

static inline void led_set(const struct device *gpio, uint32_t pin, bool on)
{
    /* Active-low LED */
    gpio_pin_set(gpio, pin, on ? 0 : 1);
}

void app_ui_set_state(app_ui_state_t state)
{
    ui_state = state;
    if (!ui_armed) {
        ui_armed = true;
        if (ld1_pwm_ready) {
            (void)pwm_set_dt(&ld1_pwm, ld1_pwm.period, 0);
        } else {
            led_set(gpio0, LD1_PIN, false);
        }
    }
}

static void ui_update_leds(uint32_t now_ms)
{
    if (!ui_armed) {
        /* Breathing effect on LD1 before any command arrives */
        const uint32_t breathe_period_ms = 2000;
        uint32_t phase = now_ms % breathe_period_ms;
        uint32_t tri = (phase < (breathe_period_ms / 2)) ?
                       phase : (breathe_period_ms - phase);
        uint32_t level = (tri * 1000U) / (breathe_period_ms / 2);

        if (ld1_pwm_ready) {
            uint64_t pulse = (uint64_t)ld1_pwm.period * level / 1000U;
            (void)pwm_set_dt(&ld1_pwm, ld1_pwm.period, (uint32_t)pulse);
        } else {
            led_set(gpio0, LD1_PIN, level > 10U);
        }

        led_set(gpio0, LD2_R_PIN, false);
        led_set(gpio1, LD2_G_PIN, false);
        led_set(gpio0, LD2_B_PIN, false);
        return;
    }

    const uint32_t slow_half_ms = 500;  /* 1 Hz blink */
    const uint32_t fast_half_ms = 125;  /* 4 Hz blink */

    bool slow_on = ((now_ms / slow_half_ms) % 2U) == 0U;
    bool fast_on = ((now_ms / fast_half_ms) % 2U) == 0U;

    /* Default: all off */
    led_set(gpio0, LD2_R_PIN, false);
    led_set(gpio1, LD2_G_PIN, false);
    led_set(gpio0, LD2_B_PIN, false);

    switch (ui_state) {
    case APP_UI_STATE_SCAN:
        led_set(gpio0, LD2_R_PIN, slow_on);
        break;
    case APP_UI_STATE_CONN:
        led_set(gpio0, LD2_B_PIN, slow_on);
        break;
    case APP_UI_STATE_START:
        led_set(gpio1, LD2_G_PIN, fast_on);
        break;
    default:
        break;
    }
}


#define SW1_GPIO_NODE DT_NODELABEL(gpio1)
#define SW1_PIN       6

static const struct device *sw1_gpio;
static bool sw1_ready;

static int sw1_init(void)
{
    sw1_gpio = DEVICE_DT_GET(SW1_GPIO_NODE);
    if (!device_is_ready(sw1_gpio)) {
        return -ENODEV;
    }

    int err = gpio_pin_configure(sw1_gpio, SW1_PIN, GPIO_INPUT | GPIO_PULL_UP);
    if (err) {
        return err;
    }

    sw1_ready = true;
    return 0;
}

static bool sw1_pressed_stable(void)
{
    if (!sw1_ready) {
        return false;
    }

    int v = gpio_pin_get(sw1_gpio, SW1_PIN);
    if (v != 0) {
        return false;
    }

    k_sleep(K_MSEC(30));
    return gpio_pin_get(sw1_gpio, SW1_PIN) == 0;
}

int main(void)
{
    int err;

    err = cdc_async_init();
    if (err) {
        /* 警告一下，但不要退出，让后面的 BLE / 按钮照样跑 */
        printk("cdc_async_init failed: %d\n", err);
    }

    cdc_printf("\r\n=== BioSpur Central Multi-NUS (buttons + CMD) ===\r\n");

    err = leds_init();
    if (err) {
        cdc_printf("leds_init err %d\r\n", err);
    }

    err = sw1_init();
    if (err) {
        cdc_printf("SW1 init err %d\r\n", err);
    }

    err = app_ble_init();
    if (err) {
        cdc_printf("app_ble_init err %d\r\n", err);
    }

    app_cmd_init();

    cdc_printf("Boot OK. SW1=reboot (P1.06).\r\n");

    while (1) {
        uint32_t now_ms = (uint32_t)k_uptime_get();
        ui_update_leds(now_ms);

        if (sw1_pressed_stable()) {
            cdc_printf("SW1: reboot\r\n");
            k_sleep(K_MSEC(10));
            sys_reboot(SYS_REBOOT_COLD);
        }

        k_sleep(K_MSEC(10));
    }

    return 0;
}
