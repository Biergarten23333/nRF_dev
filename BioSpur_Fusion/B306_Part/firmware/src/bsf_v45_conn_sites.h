/*
 * bsf_v45_conn_sites.h -- an id for every bt_conn_set_state() call site.
 *
 * R4/A2. The BSF6C53 wedge of 2026-08-09 ended with the host connection object
 * released (state=DISCONNECTED, ref=0) and no `disconnected` callback, and the
 * dump could not say WHICH line released it. These ids exist so the next corpse
 * answers that without inference.
 *
 * GENERATED from the PATCHED SDK sources -- which is what actually compiles.
 * Two earlier attempts got the count wrong: first 16 (from a truncated grep),
 * then 23 (from the PRISTINE sources, before the existing v45 patch adds a
 * site). It is 24. Deriving it from the thing that compiles is the fix.
 *
 * APPEND ONLY, never renumber: a corpse decoded against renumbered ids names the
 * wrong line, which is worse than naming none. The line numbers below are
 * documentation; the number is the identity.
 */
#ifndef BSF_V45_CONN_SITES_H
#define BSF_V45_CONN_SITES_H

#define BSF_V45_CONN_SITE_0   0u  /* adv.c:949 -> BT_CONN_ADV_CONNECTABLE */
#define BSF_V45_CONN_SITE_1   1u  /* adv.c:963 -> BT_CONN_ADV_DIR_CONNECTABLE */
#define BSF_V45_CONN_SITE_2   2u  /* adv.c:982 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_3   3u  /* adv.c:1100 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_4   4u  /* adv.c:1350 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_5   5u  /* adv.c:1543 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_6   6u  /* adv.c:1679 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_7   7u  /* conn.c:941 -> BT_CONN_DISCONNECT_COMPLETE */
#define BSF_V45_CONN_SITE_8   8u  /* conn.c:945 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_9   9u  /* conn.c:2036 -> BT_CONN_DISCONNECTING */
#define BSF_V45_CONN_SITE_10  10u  /* conn.c:2059 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_11  11u  /* conn.c:2549 -> BT_CONN_INITIATING */
#define BSF_V45_CONN_SITE_12  12u  /* conn.c:3795 -> BT_CONN_INITIATING_FILTER_LIST */
#define BSF_V45_CONN_SITE_13  13u  /* conn.c:3801 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_14  14u  /* conn.c:3832 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_15  15u  /* conn.c:3926 -> BT_CONN_SCAN_BEFORE_INITIATING */
#define BSF_V45_CONN_SITE_16  16u  /* conn.c:3931 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_17  17u  /* conn.c:3942 -> BT_CONN_INITIATING */
#define BSF_V45_CONN_SITE_18  18u  /* conn.c:3947 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_19  19u  /* conn.c:3998 -> BT_CONN_INITIATING */
#define BSF_V45_CONN_SITE_20  20u  /* conn.c:4003 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_21  21u  /* conn.c:4052 -> BT_CONN_DISCONNECTED */
#define BSF_V45_CONN_SITE_22  22u  /* conn.c:4061 -> BT_CONN_SCAN_BEFORE_INITIATING */
#define BSF_V45_CONN_SITE_23  23u  /* conn.c:4299 -> BT_CONN_SCAN_BEFORE_INITIATING */

#define BSF_V45_CONN_SITE__USED  24u

/* send_buf() failure origins, carried in BSF_V45_TX_SEND_FAIL arg1 */
#define BSF_V45_SEND_SITE_EMSGSIZE      0u   /* buf->len == 0              */
#define BSF_V45_SEND_SITE_EIO           1u   /* bt_buf_has_view            */
#define BSF_V45_SEND_SITE_ENOMEM_PKTS   2u   /* no controller buffer       */
#define BSF_V45_SEND_SITE_ENOMEM_TX     3u   /* conn_tx_alloc failed       */

#endif /* BSF_V45_CONN_SITES_H */
