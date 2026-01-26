/* src/ads1298.c
 *
 * ADS1298 + nRF52840 DK —— 连续采样稳定版（内部时钟，手动 CS）
 *  - 时序：RESET→SDATAC→WREG(配置)→RREG(核对)→START→RDATAC
 *  - DRDY 下降沿中断，每次读 27B：status[3] + 8*3
 *  - 以 0xAA 开头、CRC16-CCITT(FALSE) 收尾的 31B 帧经 USB CDC 输出
 *  - 另带 RTT 打印 + DRDY 速率监视线程
 */

#include "ads1298.h"
#include "app_config.h"
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>

#include <string.h>
#include <stdint.h>
#include <limits.h>

LOG_MODULE_REGISTER(app, LOG_LEVEL_INF);

/* ========================= 可调参数 ========================= */
#define SPI_HZ              1000000U   /* 1MHz（寄存器写时也用到这个值） */
#define DRDY_TIMEOUT_MS     20
#define PREFILL_DROP_FRAMES 10
#define CS_GUARD_US         10         /* CS 前后保护 */
#define DROP_IF_CDC_FULL    1

/* [RTT debug] 打印频率控制：每 N 帧打印一次 */
#define RTT_DEBUG_ENABLE         1
#define RTT_DEBUG_EVERY_N_FRAMES 50

/* ADS1298 自检（内部测试信号） */
#define ADS_SELF_TEST_ENABLE 1
#define ADS_SELF_TEST_FRAMES 64
#define ADS_SELF_TEST_SKIP   4

/* CONFIG1（HR=1，正确的 DR→SPS 映射）
 * 使用 app_config.h 里定义的 EMG_SAMPLE_RATE_SPS
 */

#if   (EMG_SAMPLE_RATE_SPS == 4000)
  #define CONFIG1_VALUE 0x83  /* HR=1, DR=011 */
#elif (EMG_SAMPLE_RATE_SPS == 2000)
  #define CONFIG1_VALUE 0x84  /* HR=1, DR=100 */
#elif (EMG_SAMPLE_RATE_SPS == 1000)
  #define CONFIG1_VALUE 0x85  /* HR=1, DR=101 */
#elif (EMG_SAMPLE_RATE_SPS == 500)
  #define CONFIG1_VALUE 0x86  /* HR=1, DR=110 */
#elif (EMG_SAMPLE_RATE_SPS == 250)
  #define CONFIG1_VALUE 0x87  /* HR=1, DR=111 */
#else
  #error "Unsupported EMG_SAMPLE_RATE_SPS for ADS1298"
#endif


/* CONFIG2：按手册保留位要求，常用 0x40（测试信号关闭） */
#define CONFIG2_VALUE 0x40

/* CONFIG3：
 * - PD_REFBUF(7)=1 打开内部参考缓冲
 * - RLDREF_INT(3)=1 RLD 参考选内部
 * - PD_RLD(2)=1 打开 RLD
 *   -> 0x8C = 1000_1100b：PD_REFBUF=1, RLDREF_INT=1, PD_RLD=1
 */
#define CONFIG3_VALUE 0x8C

/* RLD 选择：先全开做共模偏置（CH1..8） */
#define RLD_SENSP_VALUE 0xFF
#define RLD_SENSN_VALUE 0xFF
/* =========================================================== */

/* ====== nRF52840DK 引脚（Port1） ====== */
#define PIN_CS      12   /* P1.12 -> /CS  */
#define PIN_MOSI    13   /* P1.13 -> MOSI */
#define PIN_MISO    14   /* P1.14 <- MISO */
#define PIN_SCK     15   /* P1.15 -> SCK  */
#define PIN_RESET   11   /* P1.11 -> RESET（低复位） */
#define PIN_DRDY     8   /* P1.08 <- DRDY（下降沿） */
#define PIN_START    1   /* P1.01 -> START（高启动） */

#define SPI_NODE    DT_NODELABEL(spi3)
#define GPIO1_NODE  DT_NODELABEL(gpio1)

/* 设备句柄 */
static const struct device *const spi_dev  = DEVICE_DT_GET(SPI_NODE);
static const struct device *const gpio1    = DEVICE_DT_GET(GPIO1_NODE);

/* SPI: 默认 MODE1（CPOL=0, CPHA=1），手动 CS */
#define ADS_SPI_DEFAULT_MODE 1
static const spi_operation_t spi_base_op =
    SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB;
static struct spi_config spi_cfg = {
    .frequency = SPI_HZ,
    .operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8) |
                 SPI_TRANSFER_MSB | SPI_MODE_CPHA,
    .slave = 0,
    .cs = { .gpio = {0}, .delay = 0 },
};

/* ====== ADS1298 指令 ====== */
enum {
    CMD_WAKEUP  = 0x02, CMD_STANDBY = 0x04, CMD_RESET_CMD = 0x06,
    CMD_START   = 0x08, CMD_STOP    = 0x0A, CMD_RDATAC    = 0x10,
    CMD_SDATAC  = 0x11, CMD_RDATA   = 0x12, CMD_RREG      = 0x20,
    CMD_WREG    = 0x40
};

/* ====== 寄存器地址 ====== */
#define REG_ID         0x00
#define REG_CONFIG1    0x01
#define REG_CONFIG2    0x02
#define REG_CONFIG3    0x03
#define REG_LOFF       0x04
#define REG_CH1SET     0x05   /* CH1..CH8 连续：0x05~0x0C */
#define REG_CH2SET     0x06
#define REG_CH3SET     0x07
#define REG_CH4SET     0x08
#define REG_CH5SET     0x09
#define REG_CH6SET     0x0A
#define REG_CH7SET     0x0B
#define REG_CH8SET     0x0C
#define REG_RLD_SENSP  0x0D
#define REG_RLD_SENSN  0x0E

/* CHnSET 字段（GAIN、MUX）*/
#define CH_PD_OFF      0x80
#define CH_GAIN_12    (0x03 << 4)        /* 这里只是占位，按你原来的配置 */
#define CH_MUX_NORMAL  0x00              /* Normal electrode input */
#define CH_MUX_SHORT   0x01              /* 内部短接（用于噪声测试） */
#define CH_MUX_TEST    0x05              /* 内部测试信号 */

/* ========================= 工具函数 ========================= */

static inline void cs_low(void)  { gpio_pin_set(gpio1, PIN_CS, 0); }
static inline void cs_high(void) { gpio_pin_set(gpio1, PIN_CS, 1); }

static uint16_t crc16_ccitt_false(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc ^= ((uint16_t)data[i] << 8);
        for (int b = 0; b < 8; b++) {
            if (crc & 0x8000) {
                crc = (uint16_t)((crc << 1) ^ 0x1021);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}

/* ---------- SPI 基本收发（手动 CS） ---------- */

static int spi_write_1(const uint8_t *buf, size_t len)
{
    struct spi_buf txb = { .buf = (void*)buf, .len = len };
    struct spi_buf_set tx = { .buffers = &txb, .count = 1 };
    cs_low(); k_busy_wait(CS_GUARD_US);
    int ret = spi_write(spi_dev, &spi_cfg, &tx);
    k_busy_wait(CS_GUARD_US); cs_high();
    return ret;
}

static int spi_trx(const uint8_t *txbuf, uint8_t *rxbuf, size_t n)
{
    struct spi_buf txb = { .buf = (void*)txbuf, .len = n };
    struct spi_buf rxb = { .buf = rxbuf,       .len = n };
    struct spi_buf_set txs = { .buffers = &txb, .count = 1 };
    struct spi_buf_set rxs = { .buffers = &rxb, .count = 1 };

    cs_low(); k_busy_wait(CS_GUARD_US);
    int ret = spi_transceive(spi_dev, &spi_cfg, &txs, &rxs);
    k_busy_wait(CS_GUARD_US); cs_high();
    return ret;
}

/* ---------- 指令/寄存器读写 ---------- */

static int ads_cmd(uint8_t cmd)
{
    return spi_write_1(&cmd, 1);
}

/* 快速 WREG（寄存器写） */
static __unused int ads_wreg(uint8_t addr, const uint8_t *in, size_t n)
{
    if (n == 0 || n > 32) return -EINVAL;

    uint8_t op1 = (uint8_t)(CMD_WREG | (addr & 0x1F));
    uint8_t op2 = (uint8_t)(n - 1);

    cs_low(); k_busy_wait(CS_GUARD_US);
    if (spi_write(spi_dev, &spi_cfg,
                  &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {&op1,1} }, .count=1 })) goto out_err;
    if (spi_write(spi_dev, &spi_cfg,
                  &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {&op2,1} }, .count=1 })) goto out_err;
    if (spi_write(spi_dev, &spi_cfg,
                  &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {(void*)in,n} }, .count=1 })) goto out_err;

    k_busy_wait(CS_GUARD_US); cs_high();
    return 0;

out_err:
    k_busy_wait(CS_GUARD_US); cs_high();
    return -EIO;
}

/* RREG（寄存器读） */
static int ads_rreg(uint8_t addr, uint8_t *out, size_t n)
{
    if (n == 0 || n > 32) return -EINVAL;

    uint8_t op1 = (uint8_t)(CMD_RREG | (addr & 0x1F));
    uint8_t op2 = (uint8_t)(n - 1);

    cs_low(); k_busy_wait(CS_GUARD_US);
    if (spi_write(spi_dev, &spi_cfg,
                  &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {&op1,1} }, .count=1 })) goto out_err;
    if (spi_write(spi_dev, &spi_cfg,
                  &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {&op2,1} }, .count=1 })) goto out_err;

    if (spi_transceive(spi_dev, &spi_cfg,
            &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {(void*)"\x00", n} }, .count=1 },
            &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {out, n} }, .count=1 })) goto out_err;

    k_busy_wait(CS_GUARD_US); cs_high();
    return 0;

out_err:
    k_busy_wait(CS_GUARD_US); cs_high();
    return -EIO;
}

/* ---- tSDECODE 辅助（内部时钟约 2.048MHz → 4*tCLK≈2us） ---- */
static inline void ads_tdecode_gap(void)
{
    k_busy_wait(5);  // 5us 更保险
}

/* 慢速 + 重试版本 WREG，用于对 CONFIG/CHn 寄存器写加校验 */
static int ads_wreg_slow(uint8_t addr, const uint8_t *in, size_t n)
{
    if (!n || n > 32) return -EINVAL;

    uint32_t old_hz = spi_cfg.frequency;
    spi_cfg.frequency = 1000000U;  /* 降到 1MHz */

    uint8_t op1 = (uint8_t)(CMD_WREG | (addr & 0x1F));
    uint8_t op2 = (uint8_t)(n - 1);
    int ok = -EIO;

    for (int attempt = 0; attempt < 3 && ok != 0; ++attempt) {
        cs_low(); k_busy_wait(CS_GUARD_US);

        if (spi_write(spi_dev, &spi_cfg,
                      &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {&op1,1} }, .count=1 })) goto done;
        ads_tdecode_gap();
        if (spi_write(spi_dev, &spi_cfg,
                      &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {&op2,1} }, .count=1 })) goto done;
        ads_tdecode_gap();
        if (spi_write(spi_dev, &spi_cfg,
                      &(struct spi_buf_set){ .buffers=(struct spi_buf[]){ {(void*)in,n} }, .count=1 })) goto done;

        k_busy_wait(CS_GUARD_US); cs_high();

        if (n == 1) {
            uint8_t rd = 0;
            if (ads_rreg(addr, &rd, 1) == 0 && rd == in[0]) {
                ok = 0;
                break;
            }
            LOG_WRN("WREG verify fail addr=0x%02X want=0x%02X, retry %d",
                    addr, in[0], attempt + 1);
            k_busy_wait(50);
        } else {
            ok = 0;  /* 块写不逐字校验 */
        }
        continue;

done:
        k_busy_wait(CS_GUARD_US); cs_high();
        ok = -EIO;
    }

    spi_cfg.frequency = old_hz;
    return ok;
}

/* ========================= 配置 ========================= */

static int ads_reset_and_idle(void)
{
    /* 硬复位 */
    gpio_pin_set(gpio1, PIN_RESET, 0);
    k_msleep(2);
    gpio_pin_set(gpio1, PIN_RESET, 1);
    k_msleep(5);

    gpio_pin_set(gpio1, PIN_START, 0);

    /* 软复位后等待 ≥18*tCLK（这里 200us 足够） */
    ads_cmd(CMD_RESET_CMD);
    k_sleep(K_USEC(200));

    /* 退出连续模式，方便写寄存器 */
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
    for (int m = 0; m < 4; m++) {
        spi_cfg.operation = spi_base_op | modes[m];
        for (size_t s = 0; s < ARRAY_SIZE(speeds); s++) {
            spi_cfg.frequency = speeds[s];
            k_msleep(2);

            for (int i = 0; i < 2; i++) {
                uint8_t tx[3] = { (uint8_t)(CMD_RREG | (REG_ID & 0x1F)), 0x00, 0x00 };
                uint8_t rx[3] = { 0 };
                if (spi_trx(tx, rx, sizeof(tx)) == 0) {
                    uint8_t id = rx[2];
                    LOG_INF("ID scan m=%d hz=%u: tx=%02X %02X %02X rx=%02X %02X %02X",
                            m, (unsigned int)spi_cfg.frequency,
                            tx[0], tx[1], tx[2], rx[0], rx[1], rx[2]);
                    if (id != 0x00 && id != 0xFF) {
                        LOG_INF("ADS1298 ID detected: 0x%02X (mode=%d, hz=%u)",
                                id, m, (unsigned int)spi_cfg.frequency);
                        found = true;
                    }
                } else {
                    LOG_ERR("ADS1298 ID read failed (mode=%d hz=%u)",
                            m, (unsigned int)spi_cfg.frequency);
                }
                k_msleep(2);
            }
        }
    }

    if (!found) {
        LOG_WRN("ADS1298 ID scan: no valid response");
    }

    spi_cfg.frequency = old_hz;
    spi_cfg.operation = old_op;
}

static int ads_config_all8_true_diff(void)
{
    int ret = 0;
    const uint8_t c1 = CONFIG1_VALUE;
    const uint8_t c2 = CONFIG2_VALUE;
    const uint8_t c3 = CONFIG3_VALUE;

    uint8_t chs[8];
    for (int i = 0; i < 8; i++) {
        chs[i] = (uint8_t)(CH_MUX_NORMAL | CH_GAIN_12);
    }

    /* CONFIG */
    ret |= ads_wreg_slow(REG_CONFIG1, &c1, 1);
    ret |= ads_wreg_slow(REG_CONFIG2, &c2, 1);
    ret |= ads_wreg_slow(REG_CONFIG3, &c3, 1);
    if (ret) return -EIO;

    /* CH1..CH8 块写，保持一致 */
    ret |= ads_wreg_slow(REG_CH1SET, chs, 8);
    if (ret) return -EIO;

    /* RLD */
    const uint8_t rldp = RLD_SENSP_VALUE;
    const uint8_t rldn = RLD_SENSN_VALUE;
    ret |= ads_wreg_slow(REG_RLD_SENSP, &rldp, 1);
    ret |= ads_wreg_slow(REG_RLD_SENSN, &rldn, 1);
    if (ret) return -EIO;

    k_msleep(5);

    /* 读回核对 */
    uint8_t rd[8] = {0};
    ads_rreg(REG_CH1SET, rd, 8);
    LOG_INF("CH1..8: %02X %02X %02X %02X %02X %02X %02X %02X",
            rd[0], rd[1], rd[2], rd[3], rd[4], rd[5], rd[6], rd[7]);

    if (rd[7] != chs[7]) {
        LOG_WRN("CH8 still not set (read=0x%02X expect=0x%02X).",
                rd[7], chs[7]);
    }
    return 0;
}

/* DRDY 信号量前置声明，供自检使用 */
static struct k_sem drdy_sem;
static int ads_read_frame_27B(int32_t out_ch[8], uint8_t status[3]);

static int ads_set_all_channels(uint8_t ch_val)
{
    int ret = 0;
    uint8_t chs[7];
    for (int i = 0; i < 7; i++) {
        chs[i] = ch_val;
    }

    ret |= ads_wreg_slow(REG_CH1SET, chs, 8);
    if (ret) return -EIO;

    return 0;
}

static int ads_self_test(void)
{
    int ret;
    int32_t minv[8];
    int32_t maxv[8];
    int32_t ch_code[8];
    uint8_t status[3];

    for (int i = 0; i < 8; i++) {
        minv[i] = INT32_MAX;
        maxv[i] = INT32_MIN;
    }

    LOG_INF("ADS1298 self-test: enable internal test signal");

    ads_cmd(CMD_SDATAC);
    k_sleep(K_USEC(10));
    gpio_pin_set(gpio1, PIN_START, 0);
    k_msleep(1);

    /* 开启内部测试信号 */
    uint8_t c2_test = (uint8_t)(CONFIG2_VALUE | 0x80);
    ret = ads_wreg_slow(REG_CONFIG2, &c2_test, 1);
    if (ret) {
        LOG_ERR("self-test: CONFIG2 write failed");
        goto restore;
    }

    ret = ads_set_all_channels((uint8_t)(CH_MUX_TEST | CH_GAIN_12));
    if (ret) {
        LOG_ERR("self-test: CHnSET write failed");
        goto restore;
    }

    /* 开始转换并抓取若干帧 */
    gpio_pin_set(gpio1, PIN_START, 1);
    k_sleep(K_USEC(10));
    ads_cmd(CMD_RDATAC);
    k_sleep(K_USEC(10));

    for (int i = 0; i < ADS_SELF_TEST_SKIP + ADS_SELF_TEST_FRAMES; ) {
        if (k_sem_take(&drdy_sem, K_MSEC(DRDY_TIMEOUT_MS)) != 0) {
            LOG_WRN("self-test: missed frame");
            continue;
        }
        if (ads_read_frame_27B(ch_code, status) != 0) {
            continue;
        }

        if (i >= ADS_SELF_TEST_SKIP) {
            for (int ch = 0; ch < 8; ch++) {
                if (ch_code[ch] < minv[ch]) minv[ch] = ch_code[ch];
                if (ch_code[ch] > maxv[ch]) maxv[ch] = ch_code[ch];
            }
        }
        i++;
    }

    LOG_INF("ADS1298 self-test result (min..max, LSB):");
    LOG_INF("CH1=%d..%d CH2=%d..%d CH3=%d..%d CH4=%d..%d",
            minv[0], maxv[0], minv[1], maxv[1],
            minv[2], maxv[2], minv[3], maxv[3]);
    LOG_INF("CH5=%d..%d CH6=%d..%d CH7=%d..%d CH8=%d..%d",
            minv[4], maxv[4], minv[5], maxv[5],
            minv[6], maxv[6], minv[7], maxv[7]);

restore:
    /* 恢复正常配置 */
    ads_cmd(CMD_SDATAC);
    k_sleep(K_USEC(10));
    gpio_pin_set(gpio1, PIN_START, 0);
    k_msleep(1);
    uint8_t c2_norm = CONFIG2_VALUE;
    ads_wreg_slow(REG_CONFIG2, &c2_norm, 1);
    ads_set_all_channels((uint8_t)(CH_MUX_NORMAL | CH_GAIN_12));
    return ret;
}

static int ads_start_rdatac(void)
{
    ads_cmd(CMD_SDATAC);  k_sleep(K_USEC(10));
    gpio_pin_set(gpio1, PIN_START, 1);
    k_sleep(K_USEC(10));
    ads_cmd(CMD_RDATAC);  k_sleep(K_USEC(10));
    return 0;
}

static void ads_dump_and_verify(void)
{
    uint8_t id=0, c1=0, c2=0, c3=0, rldp=0, rldn=0, ch[8]={0};
    ads_rreg(REG_ID, &id, 1);
    ads_rreg(REG_CONFIG1, &c1, 1);
    ads_rreg(REG_CONFIG2, &c2, 1);
    ads_rreg(REG_CONFIG3, &c3, 1);
    ads_rreg(REG_RLD_SENSP, &rldp, 1);
    ads_rreg(REG_RLD_SENSN, &rldn, 1);
    ads_rreg(REG_CH1SET, ch, 8);

    LOG_INF("READBACK: ID=0x%02X, C1=0x%02X, C2=0x%02X, C3=0x%02X",
            id, c1, c2, c3);
    LOG_INF("RLD_P=0x%02X, RLD_N=0x%02X", rldp, rldn);
    LOG_INF("CH1..8: %02X %02X %02X %02X %02X %02X %02X %02X",
            ch[0], ch[1], ch[2], ch[3], ch[4], ch[5], ch[6], ch[7]);

    if ((c3 & 0x04) == 0) {
        LOG_WRN("C3 bit2=0，若波形漂移严重，可尝试调整 CONFIG3_VALUE 中 RLD 位。");
    }
}

/* ========================= USB CDC（异步发送） ========================= */

#define CDC_DEV_NAME "CDC_ACM_0"
static const struct device *cdc_dev;
RING_BUF_DECLARE(tx_ring, 64 * 1024);
static struct k_sem cdc_sem;
static struct k_thread cdc_thd;
K_THREAD_STACK_DEFINE(cdc_stack, 2048);

static inline void cdc_ring_write(const uint8_t *p, size_t n)
{
#if DROP_IF_CDC_FULL
    if (ring_buf_space_get(&tx_ring) < n) return;
#endif
    ring_buf_put(&tx_ring, p, n);
    k_sem_give(&cdc_sem);
}

static void cdc_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

    uint32_t dtr = 0;
    for (;;) {
        uart_line_ctrl_get(cdc_dev, UART_LINE_CTRL_DTR, &dtr);
        if (dtr) break;
        k_msleep(50);
    }

    uint8_t buf[512];
    for (;;) {
        if (ring_buf_is_empty(&tx_ring)) {
            k_sem_take(&cdc_sem, K_MSEC(20));
            continue;
        }
        size_t n = ring_buf_get(&tx_ring, buf, sizeof(buf));
        for (size_t i = 0; i < n; ++i) {
            uart_poll_out(cdc_dev, buf[i]);
        }
    }
}

static int usb_cdc_init(void)
{
    cdc_dev = device_get_binding(CDC_DEV_NAME);
    if (!cdc_dev) {
        LOG_ERR("CDC dev not found");
        return -ENODEV;
    }
    int ret = usb_enable(NULL);
    if (ret) {
        LOG_ERR("usb_enable failed: %d", ret);
        return ret;
    }

    k_sem_init(&cdc_sem, 0, 1);
    ring_buf_reset(&tx_ring);

    k_thread_create(&cdc_thd, cdc_stack, K_THREAD_STACK_SIZEOF(cdc_stack),
                    cdc_thread, NULL, NULL, NULL,
                    5, 0, K_NO_WAIT);
    k_thread_name_set(&cdc_thd, "cdc_tx");
    return 0;
}

/* ========================= DRDY & 读帧 ========================= */

static struct gpio_callback  drdy_cb;
static atomic_t              g_drdy_cnt = ATOMIC_INIT(0);

/* DRDY 速率监视线程 */
static struct k_thread drdy_mon_thd;
K_THREAD_STACK_DEFINE(drdy_mon_stack, 1024);

static void drdy_monitor_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    while (1) {
        int32_t c = atomic_set(&g_drdy_cnt, 0);
        LOG_INF("DRDY rate: %d SPS", c);
        k_sleep(K_SECONDS(1));
    }
}

static int ads_read_frame_27B(int32_t out_ch[8], uint8_t status[3])
{
    uint8_t tx_dummy[27] = {0};
    uint8_t rx[27]       = {0};
    int ret = spi_trx(tx_dummy, rx, sizeof(rx));
    if (ret) {
        LOG_WRN("SPI xfer err=%d", ret);
        return ret;
    }

    status[0] = rx[0];
    status[1] = rx[1];
    status[2] = rx[2];

    for (int i = 0; i < 8; ++i) {
        int32_t v = ((int32_t)rx[3 + i*3] << 16) |
                    ((int32_t)rx[4 + i*3] <<  8) |
                    ((int32_t)rx[5 + i*3]);
        if (v & 0x00800000) {
            v |= 0xFF000000;  /* 24-bit 符号扩展 */
        }
        out_ch[i] = v;
    }
    return 0;
}

/* DRDY 中断：计数 + 发信号 */
static void drdy_isr(const struct device *dev,
                     struct gpio_callback *cb,
                     uint32_t pins)
{
    ARG_UNUSED(dev);
    ARG_UNUSED(cb);
    ARG_UNUSED(pins);

    atomic_inc(&g_drdy_cnt);
    k_sem_give(&drdy_sem);
}

/* ========================= 对外回调接口 ========================= */

static ads1298_frame_cb_t g_frame_cb = NULL;

void ads1298_set_frame_callback(ads1298_frame_cb_t cb)
{
    g_frame_cb = cb;
}

/* ========================= 采样线程 ========================= */

static struct k_thread ads_thd;
K_THREAD_STACK_DEFINE(ads_stack, EMG_STACK_SIZE);

static void ads_sampling_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

    uint8_t  seq = 0;
    uint8_t  frame[31];
    int32_t  ch_code[8];
    uint8_t  status[3];
    int64_t  last_ok_ms = k_uptime_get();
#if RTT_DEBUG_ENABLE
    uint32_t dbg_cnt = 0;
#endif

    while (1) {
        if (k_sem_take(&drdy_sem, K_MSEC(DRDY_TIMEOUT_MS)) != 0) {
            LOG_WRN("missed frame");
            if (k_uptime_get() - last_ok_ms > 2000) {
                /* 简单自愈：重新 RDATAC */
                ads_cmd(CMD_SDATAC); k_msleep(2);
                ads_cmd(CMD_RDATAC); k_msleep(2);
                last_ok_ms = k_uptime_get();
            }
            continue;
        }

        if (ads_read_frame_27B(ch_code, status) != 0) {
            continue;
        }
        last_ok_ms = k_uptime_get();

        /* 可选回调：给 BLE / 本地处理用 */
        if (g_frame_cb) {
            g_frame_cb(ch_code, status);
        }

#if RTT_DEBUG_ENABLE
        if ((dbg_cnt++ % RTT_DEBUG_EVERY_N_FRAMES) == 0) {
            LOG_INF("STS:%02X %02X %02X  CH1=%08X CH2=%08X CH3=%08X CH4=%08X "
                    "CH5=%08X CH6=%08X CH7=%08X CH8=%08X",
                    status[0], status[1], status[2],
                    (unsigned int)ch_code[0],
                    (unsigned int)ch_code[1],
                    (unsigned int)ch_code[2],
                    (unsigned int)ch_code[3],
                    (unsigned int)ch_code[4],
                    (unsigned int)ch_code[5],
                    (unsigned int)ch_code[6],
                    (unsigned int)ch_code[7]);
        }
#endif

        /* 打包 31B 帧：AA + seq + status[3] + 8x3B + CRC16 */
        frame[0] = 0xAA;
        frame[1] = seq++;
        frame[2] = status[0];
        frame[3] = status[1];
        frame[4] = status[2];

        for (int i = 0; i < 8; i++) {
            int32_t v = ch_code[i];
            frame[5 + 3*i + 0] = (uint8_t)((v >> 16) & 0xFF);
            frame[5 + 3*i + 1] = (uint8_t)((v >>  8) & 0xFF);
            frame[5 + 3*i + 2] = (uint8_t)( v        & 0xFF);
        }

        uint16_t crc = crc16_ccitt_false(frame, 29);
        frame[29]    = (uint8_t)(crc >> 8);
        frame[30]    = (uint8_t)(crc & 0xFF);

        /* 通过 USB CDC 发送到 PC */
        cdc_ring_write(frame, sizeof(frame));

        /* 如果你以后要在这里加 BLE NUS 发送：
         *  - 要么在 g_frame_cb 里做
         *  - 要么在这里再调用一个 ble_send_emg(frame, 31)
         */
    }
}

/* ========================= 对外 API 实现 ========================= */

int ads1298_init(void)
{
    if (!device_is_ready(spi_dev) || !device_is_ready(gpio1)) {
        LOG_ERR("spi3/gpio1 not ready");
        return -ENODEV;
    }

    /* GPIO init */
    gpio_pin_configure(gpio1, PIN_CS,    GPIO_OUTPUT_HIGH);
    gpio_pin_configure(gpio1, PIN_RESET, GPIO_OUTPUT_HIGH);
    gpio_pin_configure(gpio1, PIN_START, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure(gpio1, PIN_DRDY,  GPIO_INPUT | GPIO_PULL_UP);

    /* DRDY 中断 */
    k_sem_init(&drdy_sem, 0, 1);
    gpio_pin_interrupt_configure(gpio1, PIN_DRDY, GPIO_INT_EDGE_FALLING);
    gpio_init_callback(&drdy_cb, drdy_isr, BIT(PIN_DRDY));
    gpio_add_callback(gpio1, &drdy_cb);

    /* USB CDC（就算失败也不影响后面 RTT 测试 EMG） */
    if (usb_cdc_init() != 0) {
        LOG_WRN("USB CDC init failed, will still run with RTT only.");
    }
    k_msleep(50);

    /* ADS1298 初始化 */
    ads_reset_and_idle();

    /* Debug: try a slow ID read to validate SPI link */
    ads_read_id_debug();

    if (ads_config_all8_true_diff() != 0) {
        LOG_ERR("WREG failed");
        return -EIO;
    }
    ads_dump_and_verify();

#if ADS_SELF_TEST_ENABLE
    ads_self_test();
#endif
    ads_start_rdatac();

    /* 清空残留 DRDY 信号量 */
    k_msleep(200);
    while (k_sem_take(&drdy_sem, K_NO_WAIT) == 0) {
        /* drain */
    }

    /* 预丢弃若干帧，等模拟前端稳定 */
    int32_t tmp[8];
    uint8_t st[3];
    for (int i = 0; i < PREFILL_DROP_FRAMES; ) {
        if (k_sem_take(&drdy_sem, K_MSEC(DRDY_TIMEOUT_MS)) == 0) {
            if (ads_read_frame_27B(tmp, st) == 0) {
                i++;
            }
        } else {
            LOG_WRN("missed frame (prefill)");
        }
    }

    /* DRDY 速率监视线程 */
    k_thread_create(&drdy_mon_thd, drdy_mon_stack,
                    K_THREAD_STACK_SIZEOF(drdy_mon_stack),
                    drdy_monitor_thread, NULL, NULL, NULL,
                    6, 0, K_NO_WAIT);
    k_thread_name_set(&drdy_mon_thd, "drdy_mon");

    return 0;
}

void ads1298_start(void)
{
    /* 启动 EMG 采样线程 */
    k_thread_create(&ads_thd, ads_stack,
                K_THREAD_STACK_SIZEOF(ads_stack),
                ads_sampling_thread, NULL, NULL, NULL,
                EMG_PRIO, 0, K_NO_WAIT);
    k_thread_name_set(&ads_thd, "ads1298_samp");
}
