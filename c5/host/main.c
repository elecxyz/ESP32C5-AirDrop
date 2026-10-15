// SPDX-License-Identifier: GPL-3.0-only
// Uses OWL's unmodified AWDL implementation; see THIRD_PARTY_NOTICES.md.
#include <errno.h>
#include <getopt.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "core.h"
#include "log.h"
#include "transport.h"

static void finish(EV_P_ ev_timer *w, int events) { (void)w; (void)events; ev_break(EV_A_ EVBREAK_ALL); }
static void signal_stop(EV_P_ ev_signal *w, int events) { (void)w; (void)events; ev_break(EV_A_ EVBREAK_ALL); }
static void stats(EV_P_ ev_timer *w, int events) { (void)loop; (void)w; (void)events; c5_stats(); }

int main(int argc, char **argv) {
    const char *port = "/dev/ttyACM0";
    const char *tap = "awdlc5";
    int seconds = 600, c, verbose = 0;
    while ((c = getopt(argc, argv, "p:c:r:t:s:v")) != -1) {
        switch (c) {
        case 'p': port = optarg; break;
        case 'c': c5_channel = atoi(optarg); break;
        case 'r': c5_country = optarg; break;
        case 't': tap = optarg; break;
        case 's': seconds = atoi(optarg); break;
        case 'v': ++verbose; break;
        default: return 2;
        }
    }
    if (seconds < 1 || seconds > 600 || strlen(tap) >= IFNAMSIZ || strlen(port) >= PATH_MAX ||
        strlen(c5_country) != 2) return 2;
    struct awdl_chan channel;
    switch (c5_channel) {
    case 6: channel = CHAN_OPCLASS_6; break;
    case 36: channel = CHAN_OPCLASS_36; break;
    case 44: channel = CHAN_OPCLASS_44; break;
    case 149: channel = CHAN_OPCLASS_149; break;
    default: fprintf(stderr, "Use channel 6, 36, 44 or 149\n"); return 2;
    }
    log_set_level(verbose > 1 ? LOG_TRACE : verbose ? LOG_DEBUG : LOG_INFO);
    struct daemon_state state = {0};
    if (awdl_init(&state, port, tap, channel, NULL) < 0) {
        fprintf(stderr, "C5 initialization failed: %s\n", strerror(errno));
        return 1;
    }
    state.awdl_state.filter_rssi = 0;
    state.awdl_state.channel.strategy = AWDL_CHANSEQ_INTERSECT;
    struct ev_loop *loop = EV_DEFAULT;
    ev_timer timeout, counters;
    ev_signal sigint, sigterm;
    ev_timer_init(&timeout, finish, seconds, 0);
    ev_timer_start(loop, &timeout);
    ev_timer_init(&counters, stats, 5, 5);
    ev_timer_start(loop, &counters);
    ev_signal_init(&sigint, signal_stop, SIGINT);
    ev_signal_init(&sigterm, signal_stop, SIGTERM);
    ev_signal_start(loop, &sigint);
    ev_signal_start(loop, &sigterm);
    awdl_schedule(loop, &state);
    log_info("C5 radio ready: %s on channel %d for %d seconds", tap, c5_channel, seconds);
    ev_run(loop, 0);
    awdl_free(&state);
    return 0;
}
