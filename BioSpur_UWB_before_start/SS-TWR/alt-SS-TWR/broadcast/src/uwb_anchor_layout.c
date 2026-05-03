#include "uwb_anchor_layout.h"

#include <errno.h>
#include <string.h>

#include <zephyr/settings/settings.h>
#include <zephyr/sys/printk.h>

#include "uwb_ss_twr_shared.h"

#define UWB_ANCHOR_LAYOUT_SETTINGS_SUBTREE "anchor_layout"
#define UWB_ANCHOR_LAYOUT_SETTINGS_KEY "runtime"
#define UWB_ANCHOR_LAYOUT_SETTINGS_PATH \
	UWB_ANCHOR_LAYOUT_SETTINGS_SUBTREE "/" UWB_ANCHOR_LAYOUT_SETTINGS_KEY

static const struct uwb_anchor_pose_mm layout_defaults[UWB_MAX_ANCHORS] = {
    {0U, 'A', 0, 0, 0},
    {1U, 'B', 2563, 0, 0},
    {2U, 'C', 2533, 4420, -8},
    {3U, 'D', -243, 4300, 0},
    {4U, 'E', 32, -74, 1516},
    {5U, 'F', 2588, 137, 1512},
    {6U, 'G', 2453, 4486, 1515},
    {7U, 'H', -245, 4290, 1518},
};

static struct uwb_anchor_pose_mm layout_runtime[UWB_MAX_ANCHORS];
static bool layout_initialized;
static bool layout_settings_loaded;

static bool uwb_anchor_layout_record_valid(const struct uwb_anchor_pose_mm *layout)
{
    if (layout == NULL) {
        return false;
    }

    for (uint8_t i = 0U; i < UWB_MAX_ANCHORS; ++i) {
        if (layout[i].anchor_id != i || layout[i].label != (char)('A' + i)) {
            return false;
        }
    }

    return true;
}

static int uwb_anchor_layout_settings_set(const char *key, size_t len,
					  settings_read_cb read_cb, void *cb_arg)
{
    const char *next;
    struct uwb_anchor_pose_mm loaded[UWB_MAX_ANCHORS];
    int err;

    if (!settings_name_steq(key, UWB_ANCHOR_LAYOUT_SETTINGS_KEY, &next) ||
        next != NULL) {
        return -ENOENT;
    }

    if (len != sizeof(loaded)) {
        return -EINVAL;
    }

    err = read_cb(cb_arg, loaded, sizeof(loaded));
    if (err < 0) {
        return err;
    }
    if (err != sizeof(loaded)) {
        return -EINVAL;
    }
    if (!uwb_anchor_layout_record_valid(loaded)) {
        return -EINVAL;
    }

    memcpy(layout_runtime, loaded, sizeof(layout_runtime));
    layout_settings_loaded = true;
    return 0;
}

static int uwb_anchor_layout_settings_export(
	int (*cb)(const char *name, const void *value, size_t val_len))
{
    if (!layout_initialized || !layout_settings_loaded) {
        return 0;
    }

    return cb(UWB_ANCHOR_LAYOUT_SETTINGS_KEY,
	      layout_runtime, sizeof(layout_runtime));
}

SETTINGS_STATIC_HANDLER_DEFINE(uwb_anchor_layout_settings,
			       UWB_ANCHOR_LAYOUT_SETTINGS_SUBTREE,
			       NULL,
			       uwb_anchor_layout_settings_set,
			       NULL,
			       uwb_anchor_layout_settings_export);

void uwb_anchor_layout_init(void)
{
    if (layout_initialized) {
        return;
    }

    memcpy(layout_runtime, layout_defaults, sizeof(layout_runtime));
    layout_initialized = true;
    layout_settings_loaded = false;

    if (IS_ENABLED(CONFIG_SETTINGS)) {
        int err = settings_subsys_init();

        if (err) {
            printk("Anchor layout settings init failed: %d; using defaults\n", err);
        } else {
            err = settings_load_subtree(UWB_ANCHOR_LAYOUT_SETTINGS_SUBTREE);
            if (err) {
                printk("Anchor layout settings load failed: %d; using defaults\n", err);
            }
        }
    }

    printk("Anchor layout source=%s\n",
           layout_settings_loaded ? "settings" : "defaults");
}

bool uwb_anchor_layout_loaded_from_settings(void)
{
    uwb_anchor_layout_init();
    return layout_settings_loaded;
}

int uwb_anchor_layout_set(uint8_t anchor_id,
			  int32_t x_mm, int32_t y_mm, int32_t z_mm)
{
    uwb_anchor_layout_init();
    if (anchor_id >= UWB_MAX_ANCHORS) {
        return -EINVAL;
    }

    layout_runtime[anchor_id].anchor_id = anchor_id;
    layout_runtime[anchor_id].label = (char)('A' + anchor_id);
    layout_runtime[anchor_id].x_mm = x_mm;
    layout_runtime[anchor_id].y_mm = y_mm;
    layout_runtime[anchor_id].z_mm = z_mm;
    return 0;
}

int uwb_anchor_layout_commit(void)
{
    int err;

    uwb_anchor_layout_init();
    if (!IS_ENABLED(CONFIG_SETTINGS)) {
        return -ENOTSUP;
    }

    err = settings_save_one(UWB_ANCHOR_LAYOUT_SETTINGS_PATH,
			    layout_runtime, sizeof(layout_runtime));
    if (err == 0) {
        layout_settings_loaded = true;
        printk("Anchor layout saved to settings (%u anchors)\n",
               (unsigned int)UWB_MAX_ANCHORS);
    } else {
        printk("Anchor layout settings save failed: %d\n", err);
    }

    return err;
}

int uwb_anchor_layout_reset(void)
{
    uwb_anchor_layout_init();
    memcpy(layout_runtime, layout_defaults, sizeof(layout_runtime));
    layout_settings_loaded = false;
    return uwb_anchor_layout_commit();
}

const struct uwb_anchor_pose_mm *uwb_anchor_layout_get(uint8_t anchor_id)
{
    uwb_anchor_layout_init();
    if (anchor_id >= UWB_MAX_ANCHORS) {
        return NULL;
    }

    return &layout_runtime[anchor_id];
}

size_t uwb_anchor_layout_count(void)
{
    return UWB_MAX_ANCHORS;
}

bool uwb_anchor_layout_is_lower_plane(uint8_t anchor_id)
{
    return anchor_id < 4U;
}

bool uwb_anchor_layout_is_upper_plane(uint8_t anchor_id)
{
    return anchor_id >= 4U && anchor_id < UWB_MAX_ANCHORS;
}
