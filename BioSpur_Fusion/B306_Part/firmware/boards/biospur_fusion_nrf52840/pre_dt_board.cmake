# SPDX-License-Identifier: Apache-2.0

# nRF52840 has intentionally overlapping peripheral register nodes.
list(APPEND EXTRA_DTC_FLAGS "-Wno-unique_unit_address_if_enabled")
