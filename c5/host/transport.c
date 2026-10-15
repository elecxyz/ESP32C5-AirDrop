// SPDX-License-Identifier: GPL-3.0-or-later
// Serial backend by elecxyz, 2026. TAP setup adapted from OWL daemon/io.c:
// Copyright (C) 2018 The Open Wireless Link Project and Milan Stute.
#include <errno.h>
#include <fcntl.h>
#include <linux/if_tun.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <termios.h>
#include <unistd.h>
#include <zlib.h>
#include "io.h"
#include "log.h"
#include "transport.h"

int c5_channel = 6;
const char *c5_country = "00";
enum { HELLO=1, START=2, STOP=3, STATS=4, TX=5, ACK=128, INFO=129, COUNTERS=130, RX=131 };
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint8_t type, version;
    uint16_t length;
    uint32_t sequence;
} header_t;
static struct {
    int fd;
    uint32_t seq, awaited;
    int response;
    uint8_t bytes[65536];
    size_t used;
    struct ether_addr mac;
    uint64_t bad_crc, rejected;
} radio = {.fd=-1};

static int send_command(uint8_t type, const void *body, uint16_t length) {
    if (length > 4200) { errno = EMSGSIZE; return -1; }
    uint8_t bytes[4216];
    header_t h = {htole32(0x57413543), type, 1, htole16(length), htole32(++radio.seq)};
    memcpy(bytes, &h, sizeof(h));
    if (length) memcpy(bytes + sizeof(h), body, length);
    uint32_t crc = htole32(crc32(0, bytes, sizeof(h) + length));
    memcpy(bytes + sizeof(h) + length, &crc, 4);
    size_t size = sizeof(h) + length + 4, written = 0;
    while (written < size) {
        ssize_t n = write(radio.fd, bytes + written, size - written);
        if (n > 0) { written += n; continue; }
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            struct pollfd p = {.fd=radio.fd, .events=POLLOUT};
            if (poll(&p, 1, 50) > 0) continue;
        }
        // A partially written command will be discarded by the firmware CRC.
        return -1;
    }
    return 0;
}

static int consume(pcap_handler callback, u_char *user) {
    int count = 0;
    while (radio.used >= sizeof(header_t)) {
        header_t h;
        memcpy(&h, radio.bytes, sizeof(h));
        size_t len = le16toh(h.length), total = sizeof(h) + len + 4;
        if (le32toh(h.magic) != 0x57413543 || h.version != 1 || len > 4200) {
            memmove(radio.bytes, radio.bytes + 1, --radio.used);
            continue;
        }
        if (radio.used < total) break;
        uint32_t crc;
        memcpy(&crc, radio.bytes + total - 4, 4);
        if (le32toh(crc) != crc32(0, radio.bytes, total - 4)) {
            ++radio.bad_crc;
            memmove(radio.bytes, radio.bytes + 1, --radio.used);
            continue;
        }
        const uint8_t *body = radio.bytes + sizeof(h);
        uint32_t seq = le32toh(h.sequence);
        if (h.type == RX && len >= 32 && callback) {
            // Radiotap: flags(no FCS), rate(6M), channel, antenna signal.
            uint8_t frame[4216] = {0,0,15,0,0x2e,0,0,0,0,12};
            int ch = body[0];
            uint16_t freq = htole16(ch <= 14 ? 2407 + ch * 5 : 5000 + ch * 5);
            uint16_t flags = htole16(ch <= 14 ? 0x00c0 : 0x0140);
            memcpy(frame + 10, &freq, 2);
            memcpy(frame + 12, &flags, 2);
            frame[14] = body[1];
            memcpy(frame + 15, body + 8, len - 8);
            struct pcap_pkthdr ph = {.caplen=len + 7, .len=len + 7};
            gettimeofday(&ph.ts, NULL);
            callback(user, &ph, frame);
            ++count;
        } else if (h.type == ACK && len == 4) {
            int32_t code;
            memcpy(&code, body, 4);
            code = le32toh(code);
            if (seq == radio.awaited) radio.response = code;
            if (code) {
                ++radio.rejected;
                log_warn("C5 command %u rejected: 0x%x", seq, code);
            }
        } else if (h.type == INFO && seq == radio.awaited) {
            char text[1024];
            unsigned m[6];
            size_t n = len < sizeof(text) - 1 ? len : sizeof(text) - 1;
            memcpy(text, body, n); text[n] = 0;
            char *mac = strstr(text, "\"mac\":\"");
            if (strstr(text, "\"firmware\":\"cachydrop-c5-radio-") &&
                strstr(text, "\"protocol\":1,") && mac &&
                sscanf(mac + 7, "%x:%x:%x:%x:%x:%x", &m[0],&m[1],&m[2],&m[3],&m[4],&m[5]) == 6) {
                for (int i=0;i<6;i++) radio.mac.ether_addr_octet[i] = m[i];
                radio.response = 0;
            }
        } else if (h.type == COUNTERS) {
            log_info("C5 %.*s | USB CRC errors %llu", (int)len, body, (unsigned long long)radio.bad_crc);
        }
        radio.used -= total;
        memmove(radio.bytes, radio.bytes + total, radio.used);
    }
    return count;
}

int c5_dispatch(pcap_t *handle, int cnt, pcap_handler callback, u_char *user) {
    (void)handle; (void)cnt;
    int count = consume(callback, user);
    // Bound the work per event-loop iteration so AWDL timers can still run.
    for (int i=0;i<8;i++) {
        ssize_t n = read(radio.fd, radio.bytes + radio.used, sizeof(radio.bytes) - radio.used);
        if (n < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            log_error("C5 disconnected: %s", strerror(errno));
            exit(1); // Closing the TAP removes the experimental interface.
        }
        if (!n) {
            log_error("C5 serial port closed");
            exit(1);
        }
        radio.used += n;
        count += consume(callback, user);
    }
    return count;
}

static int command_wait(uint8_t type, const void *body, uint16_t length) {
    radio.awaited = radio.seq + 1;
    radio.response = -1;
    if (send_command(type, body, length)) return -1;
    for (int i=0;i<50 && radio.response == -1;i++) {
        struct pollfd p = {.fd=radio.fd, .events=POLLIN};
        if (poll(&p, 1, 100) > 0) c5_dispatch(NULL, 0, NULL, NULL);
    }
    return radio.response == 0 ? 0 : -1;
}

static int open_tap(struct io_state *state, const char *name) {
    if (if_nametoindex(name)) { errno = EEXIST; return -1; }
    int fd = open("/dev/net/tun", O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    struct ifreq ifr = {0};
    snprintf(ifr.ifr_name, IFNAMSIZ, "%s", name);
    ifr.ifr_flags = IFF_TAP | IFF_NO_PI;
    if (ioctl(fd, TUNSETIFF, &ifr)) { close(fd); return -1; }
    int s = socket(AF_INET6, SOCK_DGRAM, 0);
    if (s < 0) { close(fd); return -1; }
    ifr.ifr_hwaddr.sa_family = 1;
    memcpy(ifr.ifr_hwaddr.sa_data, &state->if_ether_addr, 6);
    if (ioctl(s, SIOCSIFHWADDR, &ifr)) goto fail;
    // IPv6's minimum MTU fits in the SDK's maximum 1500-byte raw TX frame.
    ifr.ifr_mtu = 1280;
    if (ioctl(s, SIOCSIFMTU, &ifr)) goto fail;
    if (ioctl(s, SIOCGIFFLAGS, &ifr)) goto fail;
    ifr.ifr_flags |= IFF_UP | IFF_RUNNING;
    if (ioctl(s, SIOCSIFFLAGS, &ifr)) goto fail;
    close(s);
    snprintf(state->host_ifname, IFNAMSIZ, "%s", name);
    state->host_ifindex = if_nametoindex(name);
    state->host_fd = fd;
    return 0;
fail:
    close(s); close(fd); return -1;
}

int io_state_init(struct io_state *state, const char *port, const char *host, const struct ether_addr *bssid) {
    (void)bssid;
    state->host_fd = -1;
    radio.fd = open(port, O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (radio.fd < 0) return -1;
    struct termios t;
    if (tcgetattr(radio.fd, &t)) goto fail;
    cfmakeraw(&t);
    t.c_cflag |= CLOCAL | CREAD;
    t.c_cflag &= ~HUPCL;
    cfsetispeed(&t, B921600); cfsetospeed(&t, B921600);
    if (tcsetattr(radio.fd, TCSANOW, &t) || ioctl(radio.fd, TIOCEXCL)) goto fail;
    tcflush(radio.fd, TCIOFLUSH);
    if (command_wait(HELLO, NULL, 0)) { errno=EPROTO; goto fail; }
    state->if_ether_addr = radio.mac;
    uint8_t start[4] = {c5_channel, c5_country[0], c5_country[1], 0};
    if (command_wait(START, start, sizeof(start))) { errno=EPROTO; goto fail; }
    state->wlan_fd = radio.fd;
    state->wlan_handle = (pcap_t *)&radio;
    state->wlan_is_file = 1; // Fixed radio; never issue nl80211 changes on PC.
    state->wlan_ifindex = 0;
    snprintf(state->wlan_ifname, sizeof(state->wlan_ifname), "%s", port);
    if (open_tap(state, host)) { send_command(STOP, NULL, 0); goto fail; }
    return 0;
fail:
    ioctl(radio.fd, TIOCNXCL);
    close(radio.fd); radio.fd=-1;
    return -1;
}

void io_state_free(struct io_state *state) {
    if (radio.fd >= 0) {
        command_wait(STOP, NULL, 0);
        ioctl(radio.fd, TIOCNXCL);
        close(radio.fd); radio.fd=-1;
    }
    if (state->host_fd >= 0) close(state->host_fd);
}

int wlan_send(const struct io_state *state, const uint8_t *buf, int len) {
    (void)state;
    if (len < 8) return -EINVAL;
    uint16_t skip; memcpy(&skip, buf + 2, 2); skip=le16toh(skip);
    if (skip < 8 || skip > len || len - skip > 1500) return -EMSGSIZE;
    return send_command(TX, buf + skip, len - skip);
}
int host_send(const struct io_state *state, const uint8_t *buf, int len) {
    return write(state->host_fd, buf, len) == len ? 0 : -errno;
}
int host_recv(const struct io_state *state, uint8_t *buf, int *len) {
    ssize_t n = read(state->host_fd, buf, *len);
    if (n < 0) return -errno;
    *len = n; return 0;
}
int __wrap_get_hostname(char *name, size_t len) {
    snprintf(name, len, "esp32c5-airdrop"); return 0;
}
void c5_stats(void) { send_command(STATS, NULL, 0); }
