# Inject resilience only into the nRF5340 network-core HCI raw image. The
# module is visible to CPUAPP as well, but deliberately contributes no source
# there.
if(CONFIG_SOC_NRF5340_CPUNET AND CONFIG_BT_HCI_RAW)
  target_sources(app PRIVATE src/net_resilience.c)
endif()
