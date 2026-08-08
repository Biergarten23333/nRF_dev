"""Shared constants and helpers for the BT-wedge forensics run.

Everything here is derived from the raw capture layout, never from prior
reports. `RUNS` is the single source of truth for which directories belong to
which run label.
"""
import os

REPO = "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion"
OUT = os.path.join(REPO, "B306_Part/logs/bt_wedge_forensics_20260808")
CACHE = os.path.join(OUT, "cache")
PLOTS = os.path.join(OUT, "plots")
NCS = "/home/zekaixiao/ncs/v2.8.0"

RUNS = {
    "N5": {
        "label": "N5 v43_selfcapture",
        "root": os.path.join(REPO, "B306_Part/logs/v43_selfcapture_20260807"),
        "run": os.path.join(REPO, "B306_Part/logs/v43_selfcapture_20260807/B5_RUN"),
        "listeners": os.path.join(REPO, "B306_Part/logs/v43_selfcapture_20260807/B5_LISTENERS"),
        "fw": "v43",
        "master_fw": "dk-v35",
    },
    "N6": {
        "label": "N6 v43_run2",
        "root": os.path.join(REPO, "B306_Part/logs/v43_run2_20260807"),
        "run": os.path.join(REPO, "B306_Part/logs/v43_run2_20260807/B_RUN"),
        "listeners": os.path.join(REPO, "B306_Part/logs/v43_run2_20260807/B_LISTENERS"),
        "fw": "v43",
        "master_fw": "dk-v35?",
    },
    "N7": {
        "label": "N7 daylight",
        "root": os.path.join(REPO, "UWB_Part/logs/daylight_20260807"),
        "run": os.path.join(REPO, "UWB_Part/logs/daylight_20260807/B_RUN"),
        "listeners": os.path.join(REPO, "UWB_Part/logs/daylight_20260807/B_LISTENERS"),
        "fw": "v43",
        "master_fw": "dk-v35",
    },
    "N8": {
        "label": "N8 v44_fleet",
        "root": os.path.join(REPO, "UWB_Part/logs/v44_fleet_20260807"),
        "run": os.path.join(REPO, "UWB_Part/logs/v44_fleet_20260807/I_RUN"),
        "listeners": os.path.join(REPO, "UWB_Part/logs/v44_fleet_20260807/I_LISTENERS"),
        "fw": "v44",
        "master_fw": "dk-v36",
    },
}

NODES = ["BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
         "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35"]

# FNV-1a/32 of the Zephyr net_buf pool name, as emitted by pool_name_hash()
# in B306_Part/firmware/src/main.c:498. Brute-forced against every
# NET_BUF_POOL*DEFINE name in the NCS v2.8.0 tree; each hit is unique.
POOL_HASH = {
    "11597b73": "acl_tx_pool",
    "858969d7": "att_pool",
    "a14875f8": "discardable_pool",
    "2de570ea": "fragments",
    "39b3fc03": "hci_cmd_pool",
    "20588eb5": "hci_rx_pool",
    "ef427c73": "pkt_pool",       # MCUmgr SMP transport (OTA), node only
    "27b70977": "sync_evt_pool",
}


def kv(rest):
    """Parse `k=v k=v ...` from the tail of a log line. Last wins."""
    d = {}
    for tok in rest.split():
        i = tok.find("=")
        if i > 0:
            d[tok[:i]] = tok[i + 1:]
    return d


def num(s, default=None):
    if s is None:
        return default
    try:
        if s.startswith("0x") or s.startswith("0X"):
            return int(s, 16)
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return default


def fusion_logs(run_dir):
    import glob
    return sorted(glob.glob(os.path.join(run_dir, "fusion_h*.log")))
