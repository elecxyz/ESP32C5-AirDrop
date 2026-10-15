// SPDX-License-Identifier: GPL-3.0-only
// CachyDrop experimental C5 radio. Application and USB protocol: elecxyz, 2026.
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "driver/usb_serial_jtag.h"
#include "esp_crc.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"
#include "freertos/task.h"

#define MAX_BODY 4200
#define MAX_FRAME 4096
#define MAGIC 0x57413543u /* C5AW on the wire */
#define DEVICE_LABEL "CachyDrop-C5-Reciever"
enum { HELLO=1, START=2, STOP=3, STATS=4, TX=5,
       ACK=128, INFO=129, COUNTERS=130, RX=131 };
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint8_t type, version;
    uint16_t length;
    uint32_t sequence;
} header_t;
typedef struct __attribute__((packed)) {
    uint8_t channel;
    int8_t rssi;
    uint16_t reserved;
    uint32_t timestamp_us;
    uint8_t frame[MAX_FRAME];
} rx_body_t;
static const uint8_t awdl_bssid[6] = {0x00,0x25,0x00,0xff,0x94,0x73};
static RingbufHandle_t rx_queue;
static SemaphoreHandle_t usb_mutex;
static uint8_t own_mac[6], channel = 6;
static volatile bool running;
static int64_t expires_us;
static uint32_t rx_total, rx_awdl, rx_dropped, rx_bad, tx_accepted, tx_rejected;
static uint32_t tx_done, tx_failed;
static uint32_t rx_duplicates, usb_short_writes;
static uint32_t tx_nomem_retries, rx_forwarded_bytes;
static struct { uint8_t src[6], type, tid; uint16_t seq; uint32_t when; bool valid; } recent[128];
static unsigned recent_next;
static uint8_t tx_mbps = 54;
static const char *stage = "idle";

static void send_message(uint8_t type, uint32_t seq, const void *body, uint16_t len) {
    uint8_t packet[sizeof(header_t) + MAX_BODY + 4];
    header_t h = {MAGIC, type, 1, len, seq};
    memcpy(packet, &h, sizeof(h));
    if (len) memcpy(packet + sizeof(h), body, len);
    uint32_t crc = esp_crc32_le(0, packet, sizeof(h) + len);
    memcpy(packet + sizeof(h) + len, &crc, 4);
    xSemaphoreTake(usb_mutex, portMAX_DELAY);
    // A finite write timeout allows an unplugged host to stop the radio.
    size_t size = sizeof(h) + len + 4;
    if (usb_serial_jtag_write_bytes(packet, size, pdMS_TO_TICKS(100)) != size) ++usb_short_writes;
    xSemaphoreGive(usb_mutex);
}

static void reply(uint32_t seq, esp_err_t result) {
    int32_t value = result;
    send_message(ACK, seq, &value, sizeof(value));
}

static void receive_frame(void *buf, wifi_promiscuous_pkt_type_t type) {
    if (!running || (type != WIFI_PKT_MGMT && type != WIFI_PKT_DATA)) return;
    wifi_promiscuous_pkt_t *pkt = buf;
    ++rx_total;
    int len = pkt->rx_ctrl.sig_len;
    if (pkt->rx_ctrl.rx_state || len < 28 || len > MAX_FRAME + 4) {
        ++rx_bad;
        return;
    }
    // The SDK length includes the four-byte FCS; transport supplies no FCS.
    len -= 4;
    if (memcmp(pkt->payload + 16, awdl_bssid, 6)) return;
    ++rx_awdl;
    // Called on the Wi-Fi task: never block on USB or allocate here.
    const uint8_t *frame = pkt->payload;
    // Other peers' unicast traffic must not consume this small USB queue.
    if ((frame[0] & 0x0c) == 0x08 && !(frame[4] & 1) &&
        memcmp(frame + 4, own_mac, 6)) return;
    uint16_t seq = frame[22] | (frame[23] << 8);
    uint8_t tid = ((frame[0] & 0x8c) == 0x88 && len >= 26) ? frame[24] & 15 : 0;
    uint32_t now = pkt->rx_ctrl.timestamp;
    if (frame[1] & 8) {
        for (unsigned i=0; i<128; ++i) {
            if (recent[i].valid && recent[i].seq == seq && recent[i].tid == tid &&
                recent[i].type == frame[0] && (uint32_t)(now - recent[i].when) < 1000000 &&
                !memcmp(recent[i].src, frame + 10, 6)) {
                ++rx_duplicates;
                return;
            }
        }
    }
    static rx_body_t item; // The SDK serializes callbacks on its Wi-Fi task.
    item.channel = pkt->rx_ctrl.channel;
    item.rssi = pkt->rx_ctrl.rssi;
    item.reserved = 0;
    item.timestamp_us = now;
    memcpy(item.frame, frame, len);
    if (xRingbufferSend(rx_queue, &item, len + 8, 0) != pdTRUE) {
        ++rx_dropped;
    } else {
        unsigned i = recent_next++ % 128;
        memcpy(recent[i].src, frame + 10, 6);
        recent[i].type = frame[0]; recent[i].tid = tid; recent[i].seq = seq;
        recent[i].when = now; recent[i].valid = true;
    }
}

static void transmit_done(const esp_80211_tx_info_t *info) {
    ++tx_done;
    if (info->tx_status != WIFI_SEND_SUCCESS) ++tx_failed;
}

static void stop_radio(void) {
    if (running) {
        running = false;
        esp_wifi_set_promiscuous(false);
        esp_wifi_stop();
    }
    // The USB writer drains pending items while stopped; reset retry history.
    memset(recent, 0, sizeof(recent));
}

static esp_err_t start_radio(const uint8_t *body, size_t len) {
    // channel, two-letter country code, rate in Mbps (0 selects 54).
    if (len != 4 || !(body[0] == 6 || body[0] == 36 || body[0] == 44 || body[0] == 149))
        return ESP_ERR_INVALID_ARG;
    if (!((body[1] == '0' && body[2] == '0') ||
          (body[1] >= 'A' && body[1] <= 'Z' && body[2] >= 'A' && body[2] <= 'Z')))
        return ESP_ERR_INVALID_ARG;
    stop_radio();
    char country[3] = {body[1], body[2], 0};
    stage = "country";
    esp_err_t err = esp_wifi_set_country_code(country, false);
    if (err) return err;
    channel = body[0];
    wifi_phy_rate_t phy_rate;
    tx_mbps = body[3] ? body[3] : 54;
    switch (tx_mbps) {
    case 6: phy_rate = WIFI_PHY_RATE_6M; break;
    case 12: phy_rate = WIFI_PHY_RATE_12M; break;
    case 24: phy_rate = WIFI_PHY_RATE_24M; break;
    case 54: phy_rate = WIFI_PHY_RATE_54M; break;
    default: return ESP_ERR_INVALID_ARG;
    }
    wifi_tx_rate_config_t rate = {
        .phymode = channel > 14 ? WIFI_PHY_MODE_11A : WIFI_PHY_MODE_11G,
        .rate = phy_rate,
    };
    stage = "tx_rate_before_start";
    esp_err_t rate_err = esp_wifi_config_80211_tx(WIFI_IF_STA, &rate);
    stage = "wifi_start";
    err = esp_wifi_start();
    if (err) return err;
    stage = "band";
    err = esp_wifi_set_band_mode(channel > 14 ? WIFI_BAND_MODE_5G_ONLY : WIFI_BAND_MODE_2G_ONLY);
    if (!err && rate_err) { stage = "tx_rate_after_start"; err = esp_wifi_config_80211_tx(WIFI_IF_STA, &rate); }
    if (!err) { stage = "power_save"; err = esp_wifi_set_ps(WIFI_PS_NONE); }
    if (!err) { stage = "channel"; err = esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE); }
    if (!err) { stage = "promiscuous"; err = esp_wifi_set_promiscuous(true); }
    if (err) { esp_wifi_stop(); return err; }
    stage = "running";
    running = true;
    expires_us = esp_timer_get_time() + 600000000LL;
    return ESP_OK;
}

static bool valid_tx(const uint8_t *frame, size_t len) {
    if (len < 24 || len > 1500 || memcmp(frame + 16, awdl_bssid, 6) ||
        memcmp(frame + 10, own_mac, 6) || (frame[1] & 0x43)) return false;
    // Only AWDL vendor action frames and non-QoS, unencrypted AWDL data.
    if (frame[0] == 0xd0)
        return len >= 40 && !memcmp(frame + 24, "\x7f\x00\x17\xf2\x08", 5);
    if (frame[0] == 0x08)
        return len >= 40 && !memcmp(frame + 24, "\xaa\xaa\x03\x00\x17\xf2\x08\x00", 8);
    return false;
}

static void handle(const header_t *h, const uint8_t *body) {
    char json[768];
    switch (h->type) {
    case HELLO:
        snprintf(json, sizeof(json),
            "{\"firmware\":\"cachydrop-c5-radio-0.3\",\"protocol\":1,"
            "\"device_name\":\"" DEVICE_LABEL "\","
            "\"mac\":\"%02x:%02x:%02x:%02x:%02x:%02x\",\"max_tx\":1500,"
            "\"ack_support\":\"unverified\",\"stage\":\"%s\",\"channel\":%u,\"running\":%s}",
            own_mac[0],own_mac[1],own_mac[2],own_mac[3],own_mac[4],own_mac[5],
            stage, channel, running ? "true":"false");
        send_message(INFO, h->sequence, json, strlen(json));
        break;
    case START: reply(h->sequence, start_radio(body, h->length)); break;
    case STOP: stop_radio(); reply(h->sequence, ESP_OK); break;
    case STATS:
        snprintf(json, sizeof(json),
            "{\"channel\":%u,\"running\":%s,\"rx_total\":%lu,\"rx_awdl\":%lu,"
            "\"rx_dropped\":%lu,\"rx_bad\":%lu,\"tx_accepted\":%lu,"
            "\"tx_rejected\":%lu,\"tx_done\":%lu,\"tx_failed\":%lu,"
            "\"rx_duplicates\":%lu,\"usb_short_writes\":%lu,\"free_heap\":%lu,"
            "\"min_heap\":%lu,\"tx_nomem_retries\":%lu,\"rx_forwarded_bytes\":%lu,\"tx_mbps\":%u}",
            channel,running ? "true":"false",(unsigned long)rx_total,
            (unsigned long)rx_awdl,(unsigned long)rx_dropped,(unsigned long)rx_bad,
            (unsigned long)tx_accepted,(unsigned long)tx_rejected,
            (unsigned long)tx_done,(unsigned long)tx_failed,
            (unsigned long)rx_duplicates,(unsigned long)usb_short_writes,
            (unsigned long)esp_get_free_heap_size(),
            (unsigned long)esp_get_minimum_free_heap_size(),
            (unsigned long)tx_nomem_retries,(unsigned long)rx_forwarded_bytes,tx_mbps);
        send_message(COUNTERS, h->sequence, json, strlen(json));
        break;
    case TX: {
        esp_err_t err = ESP_ERR_INVALID_STATE;
        if (running && valid_tx(body, h->length)) {
            // A burst can temporarily exhaust the driver's TX buffers. Yield
            // briefly for completions instead of silently losing TCP ACKs.
            for (unsigned attempt = 0; attempt < 21; ++attempt) {
                err = esp_wifi_80211_tx(WIFI_IF_STA, body, h->length, false);
                if (err != ESP_ERR_NO_MEM || attempt == 20) break;
                ++tx_nomem_retries;
                vTaskDelay(pdMS_TO_TICKS(1));
            }
        } else if (running) err = ESP_ERR_INVALID_ARG;
        if (err) ++tx_rejected; else ++tx_accepted;
        reply(h->sequence, err);
        break;
    }
    default: reply(h->sequence, ESP_ERR_NOT_SUPPORTED);
    }
}

static void usb_receive(void *unused) {
    (void)unused;
    uint8_t buffer[8192];
    size_t used = 0;
    for (;;) {
        int n = usb_serial_jtag_read_bytes(buffer + used, sizeof(buffer) - used, pdMS_TO_TICKS(20));
        if (n > 0) used += n;
        while (used >= sizeof(header_t)) {
            header_t h;
            memcpy(&h, buffer, sizeof(h));
            if (h.magic != MAGIC || h.version != 1 || h.length > MAX_BODY) {
                memmove(buffer, buffer + 1, --used);
                continue;
            }
            size_t total = sizeof(h) + h.length + 4;
            if (used < total) break;
            uint32_t crc;
            memcpy(&crc, buffer + total - 4, 4);
            if (crc == esp_crc32_le(0, buffer, total - 4)) {
                handle(&h, buffer + sizeof(h));
                used -= total;
                memmove(buffer, buffer + total, used);
            } else {
                memmove(buffer, buffer + 1, --used);
            }
        }
        if (running && esp_timer_get_time() >= expires_us) stop_radio();
    }
}

static void usb_transmit(void *unused) {
    (void)unused;
    for (;;) {
        size_t size;
        void *item = xRingbufferReceive(rx_queue, &size, pdMS_TO_TICKS(100));
        if (item) {
            if (running) {
                send_message(RX, 0, item, size);
                rx_forwarded_bytes += size - 8;
            }
            vRingbufferReturnItem(rx_queue, item);
        }
    }
}

void app_main(void) {
    // Leave headroom for the Wi-Fi driver's dynamic RX/TX allocations.
    rx_queue = xRingbufferCreate(64 * 1024, RINGBUF_TYPE_NOSPLIT);
    usb_mutex = xSemaphoreCreateMutex();
    assert(rx_queue && usb_mutex);
    usb_serial_jtag_driver_config_t usb = {.tx_buffer_size=16384, .rx_buffer_size=16384};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb));
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    cfg.nvs_enable = 0;
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, own_mac));
    wifi_promiscuous_filter_t filter = {.filter_mask=WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA};
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&filter));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(receive_frame));
    ESP_ERROR_CHECK(esp_wifi_register_80211_tx_cb(transmit_done));
    assert(xTaskCreate(usb_receive, "c5_usb_rx", 20480, NULL, 5, NULL) == pdPASS);
    assert(xTaskCreate(usb_transmit, "c5_usb_tx", 8192, NULL, 6, NULL) == pdPASS);
}
