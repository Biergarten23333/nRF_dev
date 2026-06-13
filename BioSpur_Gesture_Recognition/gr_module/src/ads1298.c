#include "ads1298.h"
#include "app_config.h"

#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#define SPI_HZ 1000000U
#define DRDY_TIMEOUT_MS 20
#define PREFILL_DROP_FRAMES 10
#define CS_GUARD_US 10

#define ADS_DEBUG_EVERY_N_FRAMES 500U
#define ADS_SELF_TEST_ENABLE 0
#define ADS_SELF_TEST_FRAMES 64
#define ADS_SELF_TEST_SKIP 4

#if (EMG_SAMPLE_RATE_SPS == 4000)
#define CONFIG1_VALUE 0x83
#elif (EMG_SAMPLE_RATE_SPS == 2000)
#define CONFIG1_VALUE 0x84
#elif (EMG_SAMPLE_RATE_SPS == 1000)
#define CONFIG1_VALUE 0x85
#elif (EMG_SAMPLE_RATE_SPS == 500)
#define CONFIG1_VALUE 0x86
#elif (EMG_SAMPLE_RATE_SPS == 250)
#define CONFIG1_VALUE 0x87
#else
#error "Unsupported EMG_SAMPLE_RATE_SPS for ADS1298"
#endif

#define CONFIG2_VALUE 0x40
#define CONFIG3_VALUE 0x8C
#define RLD_SENSP_VALUE 0xFF
#define RLD_SENSN_VALUE 0xFF

#define PIN_CS 12
#define PIN_RESET 11
#define PIN_DRDY 8
#define PIN_START 1

#define SPI_NODE DT_NODELABEL(spi3)
#define GPIO1_NODE DT_NODELABEL(gpio1)

static const struct device *const spi_dev = DEVICE_DT_GET(SPI_NODE);
static const struct device *const gpio1 = DEVICE_DT_GET(GPIO1_NODE);

static const spi_operation_t spi_base_op =
	SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB;

static struct spi_config spi_cfg = {
	.frequency = SPI_HZ,
	.operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8) |
		     SPI_TRANSFER_MSB | SPI_MODE_CPHA,
	.slave = 0,
	.cs = { .gpio = { 0 }, .delay = 0 },
};

enum {
	CMD_RESET_CMD = 0x06,
	CMD_RDATAC = 0x10,
	CMD_SDATAC = 0x11,
	CMD_RREG = 0x20,
	CMD_WREG = 0x40,
};

#define REG_ID 0x00
#define REG_CONFIG1 0x01
#define REG_CONFIG2 0x02
#define REG_CONFIG3 0x03
#define REG_CH1SET 0x05
#define REG_RLD_SENSP 0x0D
#define REG_RLD_SENSN 0x0E

#define CH_GAIN_12 (0x03 << 4)
#define CH_MUX_NORMAL 0x00
#define CH_MUX_TEST 0x05

static struct k_sem drdy_sem;
static struct gpio_callback drdy_cb;
static atomic_t drdy_count = ATOMIC_INIT(0);
static ads1298_frame_cb_t frame_cb;

static inline void cs_low(void)
{
	gpio_pin_set(gpio1, PIN_CS, 0);
}

static inline void cs_high(void)
{
	gpio_pin_set(gpio1, PIN_CS, 1);
}

static int spi_write_1(const uint8_t *buf, size_t len)
{
	struct spi_buf txb = { .buf = (void *)buf, .len = len };
	struct spi_buf_set tx = { .buffers = &txb, .count = 1 };
	int ret;

	cs_low();
	k_busy_wait(CS_GUARD_US);
	ret = spi_write(spi_dev, &spi_cfg, &tx);
	k_busy_wait(CS_GUARD_US);
	cs_high();

	return ret;
}

static int spi_trx(const uint8_t *txbuf, uint8_t *rxbuf, size_t n)
{
	struct spi_buf txb = { .buf = (void *)txbuf, .len = n };
	struct spi_buf rxb = { .buf = rxbuf, .len = n };
	struct spi_buf_set txs = { .buffers = &txb, .count = 1 };
	struct spi_buf_set rxs = { .buffers = &rxb, .count = 1 };
	int ret;

	cs_low();
	k_busy_wait(CS_GUARD_US);
	ret = spi_transceive(spi_dev, &spi_cfg, &txs, &rxs);
	k_busy_wait(CS_GUARD_US);
	cs_high();

	return ret;
}

static int ads_cmd(uint8_t cmd)
{
	return spi_write_1(&cmd, 1);
}

static inline void ads_tdecode_gap(void)
{
	k_busy_wait(5);
}

static int ads_rreg(uint8_t addr, uint8_t *out, size_t n)
{
	uint8_t op1 = (uint8_t)(CMD_RREG | (addr & 0x1F));
	uint8_t op2 = (uint8_t)(n - 1U);
	uint8_t zero[32] = { 0 };

	if (n == 0U || n > sizeof(zero)) {
		return -EINVAL;
	}

	cs_low();
	k_busy_wait(CS_GUARD_US);

	if (spi_write(spi_dev, &spi_cfg,
		      &(struct spi_buf_set){
			      .buffers = (struct spi_buf[]){ { &op1, 1 } },
			      .count = 1 }) != 0) {
		goto out_err;
	}
	ads_tdecode_gap();
	if (spi_write(spi_dev, &spi_cfg,
		      &(struct spi_buf_set){
			      .buffers = (struct spi_buf[]){ { &op2, 1 } },
			      .count = 1 }) != 0) {
		goto out_err;
	}
	ads_tdecode_gap();
	if (spi_transceive(spi_dev, &spi_cfg,
			   &(struct spi_buf_set){
				   .buffers = (struct spi_buf[]){ { zero, n } },
				   .count = 1 },
			   &(struct spi_buf_set){
				   .buffers = (struct spi_buf[]){ { out, n } },
				   .count = 1 }) != 0) {
		goto out_err;
	}

	k_busy_wait(CS_GUARD_US);
	cs_high();
	return 0;

out_err:
	k_busy_wait(CS_GUARD_US);
	cs_high();
	return -EIO;
}

static int ads_wreg_slow(uint8_t addr, const uint8_t *in, size_t n)
{
	uint32_t old_hz = spi_cfg.frequency;
	uint8_t op1 = (uint8_t)(CMD_WREG | (addr & 0x1F));
	uint8_t op2 = (uint8_t)(n - 1U);
	int ok = -EIO;

	if (n == 0U || n > 32U) {
		return -EINVAL;
	}

	spi_cfg.frequency = 1000000U;

	for (int attempt = 0; attempt < 3 && ok != 0; ++attempt) {
		cs_low();
		k_busy_wait(CS_GUARD_US);

		if (spi_write(spi_dev, &spi_cfg,
			      &(struct spi_buf_set){
				      .buffers = (struct spi_buf[]){ { &op1, 1 } },
				      .count = 1 }) != 0) {
			goto done;
		}
		ads_tdecode_gap();
		if (spi_write(spi_dev, &spi_cfg,
			      &(struct spi_buf_set){
				      .buffers = (struct spi_buf[]){ { &op2, 1 } },
				      .count = 1 }) != 0) {
			goto done;
		}
		ads_tdecode_gap();
		if (spi_write(spi_dev, &spi_cfg,
			      &(struct spi_buf_set){
				      .buffers = (struct spi_buf[]){
					      { (void *)in, n } },
				      .count = 1 }) != 0) {
			goto done;
		}

		k_busy_wait(CS_GUARD_US);
		cs_high();

		if (n == 1U) {
			uint8_t rd = 0;

			if (ads_rreg(addr, &rd, 1) == 0 && rd == in[0]) {
				ok = 0;
				break;
			}
			printk("ADS1298 WREG verify fail addr=0x%02x want=0x%02x retry=%d\n",
			       addr, in[0], attempt + 1);
			k_busy_wait(50);
		} else {
			ok = 0;
		}
		continue;

done:
		k_busy_wait(CS_GUARD_US);
		cs_high();
		ok = -EIO;
	}

	spi_cfg.frequency = old_hz;
	return ok;
}

static int ads_reset_and_idle(void)
{
	gpio_pin_set(gpio1, PIN_RESET, 0);
	k_msleep(2);
	gpio_pin_set(gpio1, PIN_RESET, 1);
	k_msleep(5);

	gpio_pin_set(gpio1, PIN_START, 0);

	ads_cmd(CMD_RESET_CMD);
	k_sleep(K_USEC(200));
	ads_cmd(CMD_SDATAC);
	k_sleep(K_USEC(10));

	return 0;
}

static void ads_read_id_debug(void)
{
	uint32_t old_hz = spi_cfg.frequency;
	spi_operation_t old_op = spi_cfg.operation;
	const uint32_t speeds[] = { 100000U, 500000U, 1000000U };
	const spi_operation_t modes[] = {
		0,
		SPI_MODE_CPHA,
		SPI_MODE_CPOL,
		SPI_MODE_CPOL | SPI_MODE_CPHA,
	};
	bool found = false;

	k_msleep(2);
	for (int m = 0; m < ARRAY_SIZE(modes); m++) {
		spi_cfg.operation = spi_base_op | modes[m];
		for (size_t s = 0; s < ARRAY_SIZE(speeds); s++) {
			spi_cfg.frequency = speeds[s];
			k_msleep(2);

			uint8_t tx[3] = {
				(uint8_t)(CMD_RREG | (REG_ID & 0x1F)),
				0x00,
				0x00,
			};
			uint8_t rx[3] = { 0 };

			if (spi_trx(tx, rx, sizeof(tx)) == 0) {
				uint8_t id = rx[2];

				printk("ADS1298 ID scan m=%d hz=%u id=0x%02x\n",
				       m, (unsigned int)spi_cfg.frequency, id);
				if (id != 0x00 && id != 0xFF) {
					found = true;
				}
			}
		}
	}

	if (!found) {
		printk("ADS1298 ID scan: no valid response\n");
	}

	spi_cfg.frequency = old_hz;
	spi_cfg.operation = old_op;
}

static int ads_config_all8_true_diff(void)
{
	const uint8_t c1 = CONFIG1_VALUE;
	const uint8_t c2 = CONFIG2_VALUE;
	const uint8_t c3 = CONFIG3_VALUE;
	const uint8_t rldp = RLD_SENSP_VALUE;
	const uint8_t rldn = RLD_SENSN_VALUE;
	uint8_t chs[8];
	uint8_t rd[8] = { 0 };

	for (int i = 0; i < ARRAY_SIZE(chs); i++) {
		chs[i] = (uint8_t)(CH_MUX_NORMAL | CH_GAIN_12);
	}

	if (ads_wreg_slow(REG_CONFIG1, &c1, 1) ||
	    ads_wreg_slow(REG_CONFIG2, &c2, 1) ||
	    ads_wreg_slow(REG_CONFIG3, &c3, 1) ||
	    ads_wreg_slow(REG_CH1SET, chs, sizeof(chs)) ||
	    ads_wreg_slow(REG_RLD_SENSP, &rldp, 1) ||
	    ads_wreg_slow(REG_RLD_SENSN, &rldn, 1)) {
		return -EIO;
	}

	k_msleep(5);

	(void)ads_rreg(REG_CH1SET, rd, sizeof(rd));
	printk("ADS1298 CH1..8: %02x %02x %02x %02x %02x %02x %02x %02x\n",
	       rd[0], rd[1], rd[2], rd[3], rd[4], rd[5], rd[6], rd[7]);
	return 0;
}

static int ads_set_all_channels(uint8_t ch_val)
{
	uint8_t chs[8];

	memset(chs, ch_val, sizeof(chs));
	return ads_wreg_slow(REG_CH1SET, chs, sizeof(chs));
}

static int ads_start_rdatac(void)
{
	ads_cmd(CMD_SDATAC);
	k_sleep(K_USEC(10));
	gpio_pin_set(gpio1, PIN_START, 1);
	k_sleep(K_USEC(10));
	ads_cmd(CMD_RDATAC);
	k_sleep(K_USEC(10));

	return 0;
}

static int ads_read_frame_27b(int32_t out_ch[8], uint8_t status[3])
{
	uint8_t tx_dummy[27] = { 0 };
	uint8_t rx[27] = { 0 };
	int ret = spi_trx(tx_dummy, rx, sizeof(rx));

	if (ret != 0) {
		printk("ADS1298 SPI xfer err=%d\n", ret);
		return ret;
	}

	status[0] = rx[0];
	status[1] = rx[1];
	status[2] = rx[2];

	for (int i = 0; i < 8; ++i) {
		int32_t v = ((int32_t)rx[3 + i * 3] << 16) |
			    ((int32_t)rx[4 + i * 3] << 8) |
			    (int32_t)rx[5 + i * 3];

		if ((v & 0x00800000) != 0) {
			v |= 0xFF000000;
		}
		out_ch[i] = v;
	}

	return 0;
}

static int ads_self_test(void)
{
	int32_t minv[8];
	int32_t maxv[8];
	int32_t ch_code[8];
	uint8_t status[3];
	uint8_t c2_test = (uint8_t)(CONFIG2_VALUE | 0x80);
	uint8_t c2_norm = CONFIG2_VALUE;
	int ret;

	for (int i = 0; i < ARRAY_SIZE(minv); i++) {
		minv[i] = INT32_MAX;
		maxv[i] = INT32_MIN;
	}

	printk("ADS1298 self-test: enable internal test signal\n");
	ads_cmd(CMD_SDATAC);
	k_sleep(K_USEC(10));
	gpio_pin_set(gpio1, PIN_START, 0);
	k_msleep(1);

	ret = ads_wreg_slow(REG_CONFIG2, &c2_test, 1);
	if (ret != 0) {
		goto restore;
	}
	ret = ads_set_all_channels((uint8_t)(CH_MUX_TEST | CH_GAIN_12));
	if (ret != 0) {
		goto restore;
	}

	gpio_pin_set(gpio1, PIN_START, 1);
	k_sleep(K_USEC(10));
	ads_cmd(CMD_RDATAC);
	k_sleep(K_USEC(10));

	for (int i = 0; i < ADS_SELF_TEST_SKIP + ADS_SELF_TEST_FRAMES;) {
		if (k_sem_take(&drdy_sem, K_MSEC(DRDY_TIMEOUT_MS)) != 0) {
			continue;
		}
		if (ads_read_frame_27b(ch_code, status) != 0) {
			continue;
		}

		if (i >= ADS_SELF_TEST_SKIP) {
			for (int ch = 0; ch < 8; ch++) {
				minv[ch] = MIN(minv[ch], ch_code[ch]);
				maxv[ch] = MAX(maxv[ch], ch_code[ch]);
			}
		}
		i++;
	}

	printk("ADS1298 self-test CH1=%d..%d CH8=%d..%d\n",
	       minv[0], maxv[0], minv[7], maxv[7]);

restore:
	ads_cmd(CMD_SDATAC);
	k_sleep(K_USEC(10));
	gpio_pin_set(gpio1, PIN_START, 0);
	k_msleep(1);
	(void)ads_wreg_slow(REG_CONFIG2, &c2_norm, 1);
	(void)ads_set_all_channels((uint8_t)(CH_MUX_NORMAL | CH_GAIN_12));
	return ret;
}

static void drdy_isr(const struct device *dev, struct gpio_callback *cb,
		     uint32_t pins)
{
	ARG_UNUSED(dev);
	ARG_UNUSED(cb);
	ARG_UNUSED(pins);

	atomic_inc(&drdy_count);
	k_sem_give(&drdy_sem);
}

void ads1298_set_frame_callback(ads1298_frame_cb_t cb)
{
	frame_cb = cb;
}

static struct k_thread ads_thread;
K_THREAD_STACK_DEFINE(ads_stack, EMG_STACK_SIZE);

static void ads_sampling_thread(void *a, void *b, void *c)
{
	int32_t ch_code[8];
	uint8_t status[3];
	int64_t last_ok_ms = k_uptime_get();
	uint32_t dbg_cnt = 0;

	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	while (1) {
		if (k_sem_take(&drdy_sem, K_MSEC(DRDY_TIMEOUT_MS)) != 0) {
			printk("ADS1298 missed frame\n");
			if (k_uptime_get() - last_ok_ms > 2000) {
				ads_cmd(CMD_SDATAC);
				k_msleep(2);
				ads_cmd(CMD_RDATAC);
				k_msleep(2);
				last_ok_ms = k_uptime_get();
			}
			continue;
		}

		if (ads_read_frame_27b(ch_code, status) != 0) {
			continue;
		}
		last_ok_ms = k_uptime_get();

		if (frame_cb != NULL) {
			frame_cb(ch_code, status);
		}

		if ((dbg_cnt++ % ADS_DEBUG_EVERY_N_FRAMES) == 0U) {
			printk("ADS1298 STS:%02x %02x %02x CH1=%08x CH8=%08x\n",
			       status[0], status[1], status[2],
			       (unsigned int)ch_code[0],
			       (unsigned int)ch_code[7]);
		}
	}
}

int ads1298_init(void)
{
	if (!device_is_ready(spi_dev) || !device_is_ready(gpio1)) {
		printk("ADS1298 spi3/gpio1 not ready\n");
		return -ENODEV;
	}

	gpio_pin_configure(gpio1, PIN_CS, GPIO_OUTPUT_HIGH);
	gpio_pin_configure(gpio1, PIN_RESET, GPIO_OUTPUT_HIGH);
	gpio_pin_configure(gpio1, PIN_START, GPIO_OUTPUT_INACTIVE);
	gpio_pin_configure(gpio1, PIN_DRDY, GPIO_INPUT | GPIO_PULL_UP);

	k_sem_init(&drdy_sem, 0, 32);
	gpio_pin_interrupt_configure(gpio1, PIN_DRDY, GPIO_INT_EDGE_FALLING);
	gpio_init_callback(&drdy_cb, drdy_isr, BIT(PIN_DRDY));
	gpio_add_callback(gpio1, &drdy_cb);

	ads_reset_and_idle();
	ads_read_id_debug();

	if (ads_config_all8_true_diff() != 0) {
		printk("ADS1298 WREG failed\n");
		return -EIO;
	}

#if ADS_SELF_TEST_ENABLE
	(void)ads_self_test();
#else
	ARG_UNUSED(ads_self_test);
#endif

	ads_start_rdatac();

	k_msleep(200);
	while (k_sem_take(&drdy_sem, K_NO_WAIT) == 0) {
	}

	for (int i = 0; i < PREFILL_DROP_FRAMES;) {
		int32_t tmp[8];
		uint8_t st[3];

		if (k_sem_take(&drdy_sem, K_MSEC(DRDY_TIMEOUT_MS)) == 0 &&
		    ads_read_frame_27b(tmp, st) == 0) {
			i++;
		}
	}

	printk("ADS1298 capture ready rate=%u sps samples_per_ble_frame=%u\n",
	       EMG_SAMPLE_RATE_SPS, EMG_SAMPLES_PER_FRAME);
	return 0;
}

void ads1298_start(void)
{
	k_thread_create(&ads_thread, ads_stack, K_THREAD_STACK_SIZEOF(ads_stack),
			ads_sampling_thread, NULL, NULL, NULL,
			EMG_PRIO, 0, K_NO_WAIT);
	k_thread_name_set(&ads_thread, "ads1298");
}
