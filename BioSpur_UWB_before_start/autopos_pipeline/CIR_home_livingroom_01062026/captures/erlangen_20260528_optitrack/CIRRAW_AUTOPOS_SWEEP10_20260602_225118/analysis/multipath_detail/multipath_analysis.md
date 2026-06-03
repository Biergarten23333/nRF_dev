# Multipath-Focused CIR Analysis

The previous receiver-average envelope is only a sanity plot. It hides multipath because it averages different links, normalizes by the main peak, and uses linear scale. This analysis instead uses per-directed-link waveforms, aligns each frame to its main peak, and plots log-scale local windows.

## Metrics

- `tail_energy_over_main`: energy from +16 to +180 samples after the main peak divided by energy around the main peak. Higher means stronger delayed multipath tail.
- `late_peak_over_main`: strongest delayed peak after +16 samples divided by main peak. Higher means a visible secondary reflection peak.
- `fp_peak_delta`: main peak sample minus DW1000 first-path index. Large positive values mean the detected/strongest peak is later than the first path, often a multipath/NLOS sign.

## Most Multipath-Looking Links

|rx<-src|n|tail/main|late peak/main|SNR proxy dB|raw med mm|
|---|---:|---:|---:|---:|---:|
|F<-G|16|0.27|0.41|35.8|3408|
|G<-F|18|0.26|0.39|36.7|3370|
|E<-H|17|0.22|0.29|36.2|2773|
|H<-E|17|0.20|0.27|36.1|2751|
|C<-G|17|0.19|0.40|37.0|1568|
|G<-C|18|0.18|0.44|36.9|1588|
|F<-A|18|0.18|0.32|39.7|4577|
|A<-F|19|0.14|0.27|39.6|4608|
|E<-F|17|0.12|0.34|36.3|4162|
|F<-E|16|0.12|0.33|36.9|4240|
|B<-F|17|0.10|0.30|39.4|1512|
|F<-B|17|0.10|0.29|39.0|1564|

## Plots

- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/multipath_detail/multipath_suspicious_links_log.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/multipath_detail/tail_energy_over_main_heatmap.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/multipath_detail/late_peak_over_main_heatmap.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/multipath_detail/fp_peak_delta_heatmap.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/multipath_detail/multipath_pair_stats.csv`

## Reading the Plot

In `multipath_suspicious_links_log.png`, x=0 is the strongest peak. The red dashed line marks where I start counting delayed tail. On a clean link, the median curve drops quickly and stays near the floor. On a multipath-heavy link, you should see a slower decay or secondary bumps after the red line. The faint blue lines are individual frames, so if one reflection appears only sometimes it will show up there even if the median is smooth.
