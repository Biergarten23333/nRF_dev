# DATAFLOW_MAP — execution contexts on the B306 node

Blocking prerequisite for §1.4 and the substrate for §3. Every claim cites
`file:line` in the exact tree the boards were built from
(`/home/zekaixiao/ncs/v2.8.0`, which carries this project's own
`BSF_BT_STAGE_*` patches — verified by their presence at `hci_core.c:4269`,
`conn.c:343`, `att.c:757`). Nothing here is Zephyr folklore.

## 0. The five threads that matter

| thread | prio | what runs on it |
|---|---|---|
| **MPSL WQ** (`mpsl_work`) | cooperative, high | SDC→host receive path. `receive_signal_raise()` = `mpsl_work_submit(&receive_work)` (nrf/.../hci_driver.c:326-329) → `bt_hci_recv()`. **All priority HCI events execute inline here.** |
| **BT RX WQ** (`bt_workq`) | `CONFIG_BT_RX_PRIO=8`, stack 1024 | `rx_work_handler()` (hci_core.c:4252) — the single entry point for ACL, normal HCI events and ISO. |
| **system WQ** (`k_sys_work_q`) | (Zephyr default) | `tx_processor` / `tx_work`, `conn->tx_complete_work`, `telemetry_work` (watchdog), `deferred_work`, `reboot_work`, `stall_recovery_work`. |
| **notify worker** (`notify_worker_thread_id`) | 9 | the one and only caller of `bt_gatt_notify()` (main.c:1583-1620). |
| **publisher** (`publisher_thread_id`) | 10 | drains `q_ctl` → `q_uwb` → `q_imu` in strict priority (main.c:1636-1655) and hands one record at a time to the notify worker. |

Plus producers: `uart_parser_thread_id` (prio 5), IMU pull, `control_thread_id` (prio 5).

## 1. TX pipeline — application record to the air

```
producer thread            publish_data_record()            main.c:1506-1520
  └─ enqueue_{imu,uwb,ctl}_record()  put_drop_oldest()       main.c:1487-1503
       └─ k_sem_give(&publisher_sem)                          (never blocks)

publisher thread (prio 10)  strict ctl > uwb > imu           main.c:1636-1655
  └─ publisher_notify()                                       main.c:1543-1577
       ├─ not subscribed        -> drop_unsub, return         main.c:1560
       ├─ k_sem_take(&notify_idle_sem, K_MSEC(1200)) fails
       │                        -> notify_timeout_drop[s]     main.c:1539/1565
       └─ copy payload, k_sem_give(&notify_job_sem)

notify worker (prio 9)                                        main.c:1579-1620
  └─ bt_gatt_notify()
       └─ bt_att_chan_create_pdu(op = BT_ATT_OP_NOTIFY)       att.c:728
            switch (att_op_get_type(op)):
              ATT_RESPONSE / ATT_CONFIRMATION -> BT_ATT_TIMEOUT (30 s)
              default (INCLUDING NOTIFY)      -> **K_FOREVER**  att.c:745-748
            bt_l2cap_create_pdu_timeout(&att_pool, 0, timeout) att.c:765
       └─ chan_send() -> bt_l2cap_send_cb() -> bt_conn_send_cb()
            sys_slist_append(&conn->tx_pending, &tx->node)     conn.c:792
            bt_tx_irq_raise()                                  conn.c:919
```

**The single most consequential line in the system is `att.c:747`: a
notification allocates from the 8-buffer `att_pool` with `K_FOREVER`.** If
that pool stays empty, the notify worker parks there and never returns —
`publisher_count` and `publisher_max_us` both freeze at their last normal
values (COUNTER_SEMANTICS §1), and `notify_timeout_drop` starts counting at
one per 1.2 s per producer attempt.

## 2. Handing packets to the controller — system workqueue

```
bt_tx_irq_raise()  ->  k_work_submit(&tx_work)                hci_core.c:4790-4794
tx_processor(item)                                            hci_core.c:4771
  ├─ process_pending_cmd(K_NO_WAIT)      (non-blocking)       hci_core.c:4759
  └─ bt_conn_tx_processor()              (no blocking calls — verified by
                                          grep for K_FOREVER / k_sem_take /
                                          k_mutex / net_buf_alloc in the
                                          function body)
```

Credits: `k_sem_take(bt_conn_get_pkts(conn), K_NO_WAIT)`; out of credits, the
processor simply self-suspends (`conn.c:1164-1166` comment). **Nothing in the
TX path blocks the system workqueue.**

## 3. Completion pipeline — where the buffers come back

```
controller: HCI Number Of Completed Packets
  -> MPSL WQ: bt_hci_recv -> bt_recv_unsafe                   hci_core.c:4117-4147
       BT_HCI_EVT_FLAG_RECV_PRIO -> hci_event_prio()  INLINE  hci_core.c:4076
         handle_event(..., prio_events)                        hci_core.c:4069-4071
           hci_num_completed_packets()                         hci_core.c:578
             bt_conn_tx_notify(conn, /*wait*/ false)           conn.c:342
               caller != syswq thread, so:
               k_work_submit_to_queue(&k_sys_work_q,
                                      &conn->tx_complete_work) conn.c:355
                 (CONFIG_BT_CONN_TX_NOTIFY_WQ=n -> k_sys_work_q,
                  tx_notify_workqueue_get(), conn.c:4100-4107)

  -> system WQ: tx_complete_work -> tx_notify_process()        conn.c:1704 / 294
       pops conn->tx_complete under irq_lock                   conn.c:305-312
       tx_free(tx); then cb(conn, user_data, 0)                conn.c:325-334
         chan_sent_cb -> net_buf_unref(nb)   <-- ATT BUFFER RETURNED  att.c
       bt_tx_irq_raise()                                       conn.c:337
```

So `att_pool` buffers are freed **only** by callbacks that run on the system
workqueue, driven **only** by Number Of Completed Packets events that arrive
on the MPSL workqueue.

## 4. RX pipeline — inbound

```
MPSL WQ: bt_recv_unsafe()                                     hci_core.c:4117
  BT_BUF_ACL_IN            -> rx_queue_put()                  hci_core.c:4125
  BT_BUF_EVT, RECV_PRIO    -> hci_event_prio() inline         hci_core.c:4134
  BT_BUF_EVT, RECV         -> rx_queue_put()                  hci_core.c:4138
      rx_queue_put: net_buf_slist_put(&bt_dev.rx_queue, buf);
                    k_work_submit_to_queue(&bt_workq, &rx_work)  hci_core.c:4102-4110

BT RX WQ: rx_work_handler()                                   hci_core.c:4252
  BSF_BT_STAGE(RX_WORK_ENTER)                                 hci_core.c:4269
  ACL  -> hci_acl()  -> conn->rx reassembly -> bt_l2cap_recv -> ATT
          -> att_handle_req -> bt_att_create_rsp_pdu
             net_buf_alloc(&att_pool, BT_ATT_TIMEOUT = 30 s)  att.c:3096
  EVT  -> hci_event() -> incl. hci_disconn_complete (normal half)
  BSF_BT_STAGE(RX_WORK_EXIT)                                  hci_core.c:4300
```

Blocking primitives reachable **on the BT RX WQ**, ranked by how long they
can hold it:
1. `net_buf_alloc(&hci_cmd_pool, K_FOREVER)` — `bt_hci_cmd_create()`,
   hci_core.c:334. Pool size **2**. Unbounded.
2. `bt_att_chan_create_pdu()` on a non-response op — `K_FOREVER`, att.c:747.
3. `net_buf_alloc(&att_pool, BT_ATT_TIMEOUT)` — 30 s per attempt, att.c:3096.
4. `k_sem_take(&bt_dev.ncmd_sem / sync_sem, HCI_CMD_TIMEOUT)` — 10 s.
5. `k_work_flush(&conn->tx_complete_work, &sync)` — conn.c:361, bounded iff
   the system workqueue is running.

## 5. Disconnect and re-advertise

`BT_HCI_EVT_DISCONN_COMPLETE` carries **both** flags:
- priority half `hci_disconn_complete_prio` runs on the **MPSL WQ**
  (hci_core.c:4065),
- normal half `hci_disconn_complete` runs on the **BT RX WQ**,
  → `bt_conn_set_state(DISCONNECTED)` → `process_unack_tx()` (conn.c:1170)
  → app callback `disconnected()` (main.c) → **`start_advertising()` is
  called directly, on the BT RX WQ** (last statement of `disconnected()`).

`start_advertising()` → `bt_le_adv_start()` → `bt_hci_cmd_send_sync()` →
`bt_hci_cmd_create()` → the unbounded `hci_cmd_pool` allocation of §4.1.

**Therefore "the peer disconnected but never re-advertised" localises to
_the BT RX WQ never reaching, or never returning from, `start_advertising()`_
— and nothing narrower.** Note that this observation exists for exactly one
board (BSFEC35, the only wedged node ever force-disconnected); the other
three were never disconnected while wedged, so the test was never run on
them. It does not, by itself, distinguish "BT RX WQ
parked before the disconnect event" from "BT RX WQ parked inside the
re-advertise". §0.3's retraction stands, and this map does not repair it.

## 6. The structural consequence this map adds

`tx_processor` (TX to controller), `conn->tx_complete_work` (buffer frees and
credit return) and `telemetry_work` (**watchdog feed**) all run on the **same
single-threaded system workqueue**. `telemetry_work_handler()` feeds the
watchdog at its first statement (main.c:3232) and re-arms itself
unconditionally at its last (main.c:3420) — there is no early return in the
body. The watchdog is `WDT_FLAG_RESET_SOC`.

> **A node that stays wedged for 90 minutes without resetting proves its
> system workqueue reached the tail of `telemetry_work_handler()` about 5400
> consecutive times.** A blocked system workqueue is therefore excluded for
> every wedge event with a nonzero no-reset duration — which excludes the
> "TX processing stopped because the syswq was stuck" and "completion
> callbacks stopped because the syswq was stuck" variants of H1 outright.

What survives for H1 is the variant this map cannot touch: the syswq runs
fine but has nothing to do because **Number Of Completed Packets never
arrives**, so credits never return, `conn->tx_complete` stays empty,
`chan_sent_cb` never runs, `att_pool` stays at 0, and the notify worker parks
in `att.c:747` forever. That variant predicts a *specific* pool signature and
a *specific* master-side one (§3).

## 7. Two contexts that are NOT what earlier reports assumed

- The HCI receive context is the **MPSL workqueue**, not a dedicated
  `sdc_rx` thread. `receive_signal_raise()` submits `receive_work` to
  `mpsl_work` (nrf/.../hci_driver.c:325-329). Same conclusion for the
  multi-writer contamination of `bsf_bt_stage` in §0.3, different thread name.
- `CONFIG_BT_CONN_TX_NOTIFY_WQ=n` does **not** mean "callbacks run in the
  receive context". It means they run on the **system workqueue**
  (`tx_notify_workqueue_get()` returns `&k_sys_work_q`, conn.c:4104). Only
  the `k_work_submit` happens in the receive context.

## 8. `INSUFFICIENT` items in this map

- Whether `hci_cmd_pool` was actually exhausted at any wedge onset: not
  observable from the capture. The node reports `hci_cmd_pool` avail in
  `FUSION_POOL` **at 1 Hz**, which the §9 timing analysis will bound but
  cannot resolve at sub-second scale.
- Which buffer, if any, held `hci_rx_pool` slots: no holder instrumentation
  exists (§9, §14).
