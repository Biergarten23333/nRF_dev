# Paper Reading Notes

Generated from verified OpenAlex metadata and abstracts. Papers marked `abstract_read_via_openalex` had an abstract available and were read at abstract level; papers marked `metadata_read_via_openalex` had title/venue/DOI metadata only in the registry. The notes below emphasize the critical novelty questions requested in the prompt.

## 1. NLOS identification and mitigation for localization based on UWB experimental data

- Citation: Stefano Maranò, Wesley M. Gifford, Henk Wymeersch, Moe Z. Win. NLOS identification and mitigation for localization based on UWB experimental data. IEEE Journal on Selected Areas in Communications, 2010. https://doi.org/10.1109/jsac.2010.100907
- Cluster(s): C, D
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: Sensor networks can benefit greatly from location-awareness, since it allows information gathered by the sensors to be tied to their physical locations. Ultra-wide bandwidth (UWB) transmission is a promising technology for location-aware sensor networks, due to its power efficiency, fine delay resolution, and robust operation in harsh environments. However, the presence of walls and other obstacles presents a significant challenge in terms of localization, as they can result in positively biased distance estimates. We have performed an extensive indoor measurement campaign with FCC-compliant UWB radios to quantify the effect of non-line-of-sight (NLOS) propagation. From these channel pulse responses, we extract features that are representative of the propagation conditions. We then develop classification and regression algorithms...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 2. UWB System for Indoor Positioning and Tracking with Arbitrary Target Orientation, Optimal Anchor Location, and Adaptive NLOS Mitigation

- Citation: Yuyao Chen, Shihping Kevin Huang, Tingwei Wu, Wei‐Ting Tsai, Chong‐Yi Liou, Shau‐Gang Mao. UWB System for Indoor Positioning and Tracking with Arbitrary Target Orientation, Optimal Anchor Location, and Adaptive NLOS Mitigation. IEEE Transactions on Vehicular Technology, 2020. https://doi.org/10.1109/tvt.2020.2972578
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: The Ultra-wideband (UWB) system for indoor positioning and tracking with the characteristics of arbitrary target orientation, optimal anchor location, and adaptive non-line-of-sight (NLOS) mitigation characteristics is proposed and implemented by introducing the circularly polarized antenna, the genetic algorithm (GA), and the machine learning method. The time-domain characteristic of the UWB system using the proposed circularly polarized antennas with wide bandwidth and omnidirectional radiation is investigated by transient response. Contrary to UWB system using the conventional linearly polarized antenna, the pulse distortion is insignificant and is verified by the measured antenna performance with high signal fidelity (>0.98) and low standard deviation (STD) of time delay (<; 0.05 ns). By considering the NLOS electromagnetic wave...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 3. NLOS Identification and Weighted Least-Squares Localization for UWB Systems Using Multipath Channel Statistics

- Citation: İsmail Güvenç, Chia‐Chin Chong, Fujio Watanabe, Hiroshi Inamura. NLOS Identification and Weighted Least-Squares Localization for UWB Systems Using Multipath Channel Statistics. EURASIP Journal on Advances in Signal Processing, 2007. https://doi.org/10.1155/2008/271984
- Cluster(s): C, D, F
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: Non-line-of-sight (NLOS) identification and mitigation carry significant importance in wireless localization systems. In this paper, we propose a novel NLOS identification technique based on the multipath channel statistics such as the kurtosis, the mean excess delay spread, and the root-mean-square delay spread. In particular, the IEEE 802.15.4a ultrawideband channel models are used as examples and the above statistics are found to be well modeled by log-normal random variables. Subsequently, a joint likelihood ratio test is developed for line-of-sight (LOS) or NLOS identification. Three different weighted least-squares (WLSs) localization techniques that exploit the statistics of multipath components (MPCs) are analyzed. The basic idea behind the proposed WLS approaches is that smaller weights are given to the measurements which are...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 4. NLOS Identification and Mitigation for UWB Localization Systems

- Citation: İsmail Güvenç, Chia‐Chin Chong, Fujio Watanabe. NLOS Identification and Mitigation for UWB Localization Systems. 2007 IEEE Wireless Communications and Networking Conference, 2007. https://doi.org/10.1109/wcnc.2007.296
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: Non-line-of-sight (NLOS) identification and mitigation carries significant importance in wireless positioning systems. In this paper, the authors propose a novel NLOS identification technique based on the multipath channel statistics such as the kurtosis, the mean excess delay spread, and the root mean square delay spread. In particular, the IEEE 802.15.4a ultrawideband channel models are used as examples and the above statistics are found to be well modeled by log-normal random variables. Subsequently, a joint likelihood ratio test is developed for LOS/NLOS identification. Simulation results show that correct identification can be achieved with over 90% of the realizations for most channel models. A weighted least squares localization algorithm is also developed using the NLOS information, and accuracy gains with respect to a...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 5. Non-line-of-sight identification in ultra-wideband systems based on received signal statistics

- Citation: S. Venkatesh, R. Michael Buehrer. Non-line-of-sight identification in ultra-wideband systems based on received signal statistics. IET Microwaves Antennas & Propagation, 2007. https://doi.org/10.1049/iet-map:20060273
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: Non-line-of-sight (NLOS) propagation can severely degrade the reliability of communication and localisation accuracy in indoor ultra-wideband (UWB) ‘location-aware’ networks. Link adaptation and NLOS bias mitigation techniques have respectively been proposed to alleviate these effects, but implicitly rely on the ability to accurately distinguish between LOS and NLOS propagation scenarios. A statistical NLOS identification technique based on the hypothesis-testing of received signal parameters in UWB propagation channels is discussed. In contrast to narrowband and wideband signals, UWB signals possess higher temporal resolution and robustness to multipath fading. We show that these characteristics result in differences in the statistics of (a) the time-of-arrival (TOA), (b) the received signal strength (RSS) and (c) the...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 6. Environmental Cross-Validation of NLOS Machine Learning Classification/Mitigation with Low-Cost UWB Positioning Systems

- Citation: Valentín Barral, Carlos J. Escudero, José A. García‐Naya, Pedro Suárez-Casal. Environmental Cross-Validation of NLOS Machine Learning Classification/Mitigation with Low-Cost UWB Positioning Systems. Sensors, 2019. https://doi.org/10.3390/s19245438
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Indoor positioning systems based on radio frequency inherently present multipath-related phenomena. This causes ranging systems such as ultra-wideband (UWB) to lose accuracy when detecting secondary propagation paths between two devices. If a positioning algorithm uses ranging measurements without considering these phenomena, it will face critical errors in estimating the position. This work analyzes the performance obtained in a localization system when combining location algorithms with machine learning techniques applied to a previous classification and mitigation of the propagation effects. For this purpose, real-world cross-scenarios are considered, where the data extracted from low-cost UWB devices for training the algorithms come from a scenario different from that considered for the test. The experimental results reveal that...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 7. Robust time-of-arrival self calibration and indoor localization using Wi-Fi round-trip time measurements

- Citation: Kenneth Batstone, Magnus Oskarsson, Kalle Åström. Robust time-of-arrival self calibration and indoor localization using Wi-Fi round-trip time measurements. 2016 IEEE International Conference on Communications Workshops (ICC), 2016. https://doi.org/10.1109/iccw.2016.7503759
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: The problem of estimating receiver-sender node positions from measured receiver-sender distances is a key issue in different applications such as microphone array calibration, radio antenna array calibration, mapping and positioning using UWB and mapping and positioning using round-trip-time measurements between mobile phones and Wi-Fi-units. Thanks to recent research in this area we have an increased understanding of the geometry of this problem. In this paper, we study the problem of missing information and the presence of outliers in the given data. We propose a novel hypothesis and test framework that efficiently finds initial estimates of the unknown parameters and combine such methods with optimization techniques to obtain accurate and robust systems. The proposed systems are evaluated using Wi-Fi round-trip time measurements to...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 8. Self-calibration and Collaborative Localization for UWB Positioning Systems

- Citation: Matteo Ridolfi, Abdil Kaya, Rafael Berkvens, Maarten Weyn, Wout Joseph, Eli De Poorter. Self-calibration and Collaborative Localization for UWB Positioning Systems. ACM Computing Surveys, 2021. https://doi.org/10.1145/3448303
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-Wideband (UWB) is a Radio Frequency technology that is currently used for accurate indoor localization. However, the cost of deploying such a system is large, mainly due to the need for manually measuring the exact location of the installed infrastructure devices (“anchor nodes”). Self-calibration of UWB reduces deployment costs, because it allows for automatic updating of the coordinates of fixed nodes when they are installed or moved. Additionally, installation costs can also be reduced by using collaborative localization approaches where mobile nodes act as anchors. This article surveys the most significant research that has been done on self-calibration and collaborative localization. First, we find that often these terms are improperly used, leading to confusion for the readers. Furthermore, we find that in most of the...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 9. Design and Implementation of Synchronization-free TDOA Localization System Based on UWB

- Citation: W. Wang, Jun Huang, Shaotang Cai, Jifeng Yang. Design and Implementation of Synchronization-free TDOA Localization System Based on UWB. Radioengineering, 2019. https://doi.org/10.13164/re.2019.0320
- Cluster(s): A, B
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: At present, indoor localization system based on ultra-wideband (UWB) has attracted more and more attention. In UWB system, Time Difference of Arrival (TDOA) and Two-Way Ranging (TWR) are widely used. However, TDOA requires high-accuracy time synchronization between all anchor nodes and even slight noise can cause large localization error. In TWR, although two-way communication between anchor nodes with known location and blind nodes to be located can avoid the time synchronization issue effectively, the clock drift and the number of blind nodes will affect the system performance. To overcome these problems, a new synchronization-free TDOA location algorithm is proposed. Firstly,the clock model is established and the influence of antenna delay is considered. Then, the system signal exchange mechanism and localization model are...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 10. Calibration and Uncertainty Characterization for Ultra-Wideband Two-Way-Ranging Measurements

- Citation: Mohammed Shalaby, Charles Champagne Cossette, James Richard Forbes, Jérôme Le Ny. Calibration and Uncertainty Characterization for Ultra-Wideband Two-Way-Ranging Measurements. 2023 IEEE International Conference on Robotics and Automation (ICRA), 2023. https://doi.org/10.1109/icra48891.2023.10160769
- Cluster(s): B
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: Ultra-Wideband (UWB) systems are becoming increasingly popular for indoor localization, where range measurements are obtained by measuring the time-of-flight of radio signals. However, the range measurements typically suffer from a systematic error or bias that must be corrected for high-accuracy localization. In this paper, a ranging protocol is proposed alongside a robust and scalable antenna-delay calibration procedure to accurately and efficiently calibrate antenna delays for many UWB tags. Additionally, the bias and uncertainty of the measurements are modelled as a function of the received-signal power. The full calibration procedure is presented using experimental training data of 3 aerial robots fitted with 2 UWB tags each, and then evaluated on 2 test experiments. A localization problem is then formulated on the experimental...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 11. NLOS identification and mitigation based on CIR with particle filter

- Citation: Zhuoqi Zeng, Rubing Bai, Lei Wang, Steven Liu. NLOS identification and mitigation based on CIR with particle filter. 2019 IEEE Wireless Communications and Networking Conference (WCNC), 2019. https://doi.org/10.1109/wcnc.2019.8886002
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: As key factors to guarantee accurate localization for ultra-wide band system (UWB), Non-line-of-sight (NLOS) identification and mitigation attract lots of attentions. One of the most effective methods for NLOS detection is based on the different characters of channel impulse response (CIR) under Line-of-sight (LOS) and NLOS condition. Features (such as kurtosis, standard deviation, energy, etc.) extracted from CIR are used for classification with the help of machine learning algorithm. Different from existing approaches, the NLOS and LOS probability density functions (PDF) of the correlation coefficient are calculated with the training data. The probability that the CIR is measured under LOS or NLOS is determined based on the PDF. A weighted particle filter is proposed to reduce the localization error, caused by NLOS. The weights for...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 12. Indoor Drone Positioning: Accuracy and Cost Trade-Off for Sensor Fusion

- Citation: Jono Vanhie-Van Gerwen, Kurt Geebelen, Jia Wan, Wout Joseph, Jeroen Hoebeke, Eli De Poorter. Indoor Drone Positioning: Accuracy and Cost Trade-Off for Sensor Fusion. IEEE Transactions on Vehicular Technology, 2021. https://doi.org/10.1109/tvt.2021.3129917
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Indoor drone or Unmanned Aerial Vehicle (UAV) operations, automated or with pilot control, are an upcoming and exciting subset of drone use cases. Automated indoor flights tighten the requirements of stability and localization accuracy in comparison with the classic outdoor use cases which rely primarily on (RTK) GNSS for localization. In this paper the effect of multiple sensors on 3D indoor position accuracy is investigated using the flexible sensor fusion platform OASE. This evaluation is based on real-life drone flights in an industrial lab with mm-accurate ground truth measurements provided by motion capture cameras, allowing the evaluation of the sensors based on their deviation from the ground truth in 2D and 3D. The sensors under consideration for this research are: IMU, sonar, SLAM camera, ArUco markers and Ultra-Wideband...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 13. A robust UWB indoor positioning system for highly complex environments

- Citation: Enrique García, Pablo Poudereux, Álvaro Hernández, Jesús Ureña, David Gualda. A robust UWB indoor positioning system for highly complex environments. 2015 IEEE International Conference on Industrial Technology (ICIT), 2015. https://doi.org/10.1109/icit.2015.7125601
- Cluster(s): C, F
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: Ultra-Wideband (UWB) has a high interest in research and industry for accurate indoor positioning. This technology comprises signals with a bandwidth of at least 500 MHz at a power decay of −10dB. This large bandwidth con-feres the capability of resolving multipath, penetrating through obstacles and accurate ranging. However, NLOS (Non-Line-Of-Sight) conditions produced by obstacles in indoor environments severely degrade performance, as they introduce a positive bias in ranging estimation. In this paper we present a robust UWB indoor positioning which is able to accurately operate in a highly complex indoor scenario, where NLOS condition is predominant. For that purpose, the system uses a NLOS detection algorithm based on the skewness of the estimated channel impulse response and it mitigates NLOS by using an Extended Kalman Filter....
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 14. A Self-Calibrating Localization Solution for Sport Applications with UWB Technology

- Citation: Marco Piavanini, Luca Barbieri, Mattia Brambilla, Mattia Cerutti, Simone Ercoli, Andrea Agili, Monica Nicoli. A Self-Calibrating Localization Solution for Sport Applications with UWB Technology. Sensors, 2022. https://doi.org/10.3390/s22239363
- Cluster(s): A, B
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: This study addressed the problem of localization in an ultrawide-band (UWB) network, where the positions of both the access points and the tags needed to be estimated. We considered a fully wireless UWB localization system, comprising both software and hardware, featuring easy plug-and-play usability for the consumer, primarily targeting sport and leisure applications. Anchor self-localization was addressed by two-way ranging, also embedding a Gauss-Newton algorithm for the estimation and compensation of antenna delays, and a modified isolation forest algorithm working with low-dimensional set of measurements for outlier identification and removal. This approach avoids time-consuming calibration procedures, and it enables accurate tag localization by the multilateration of time difference of arrival measurements. For the assessment of...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 15. UWB Sensor-Based Indoor LOS/NLOS Localization With Support Vector Machine Learning

- Citation: Hongchao Yang, Yunjia Wang, Chee Kiat Seow, Meng Sun, Minghao Si, Lu Huang. UWB Sensor-Based Indoor LOS/NLOS Localization With Support Vector Machine Learning. IEEE Sensors Journal, 2023. https://doi.org/10.1109/jsen.2022.3232479
- Cluster(s): C, D
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultrawideband (UWB) sensor technology is known to achieve high-precision indoor localization accuracy in line-of-sight (LOS) environments, but its localization accuracy and stability suffer detrimentally in non-LOS (NLOS) conditions. Current NLOS/LOS identification based on channel impulse response’s (CIR) characteristic parameters (CCPs) improves location accuracy, but most CIR-based identification approaches did not sufficiently exploit the CIR information and are environment specific. This article derives three new CCPs and proposes a novel two-step identification/classification methodology with dynamic threshold comparison (DTC) and the fuzzy credibility-based support vector machine (FC-SVM). The proposed support vector machine (SVM)-based classification methodology leverages the derived CCPs obtained from the waveform and its...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 16. Real-time identification of NLOS range measurements for enhanced UWB localization

- Citation: Karthikeyan Gururaj, Anojh Kumaran Rajendra, Yang Song, Choi Look Law, Guofa Cai. Real-time identification of NLOS range measurements for enhanced UWB localization. 2017 International Conference on Indoor Positioning and Indoor Navigation (IPIN), 2017. https://doi.org/10.1109/ipin.2017.8115877
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: Despite of the ultra-wideband (UWB) system's robustness against multipath in cluttered environments, a number of challenges remain before UWB localization can be implemented. In particular, non-line-of-sight (NLOS) propagation is especially critical for high-resolution localization systems because non-negligibly positive biases will be introduced in distance measurements, thus degrading the localization performance. Here, based on received and first path powers obtainable from channel impulse response (CIR), we propose a simple but very efficient method to distinguish between NLOS and LOS conditions. Our method needs neither the training data nor the prior knowledge about the environments, thus enabling realtime NLOS identification. Despite the simplicity of our method, the experimental results verify its speediness and highly correct...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 17. A Machine Learning Approach to Ranging Error Mitigation for UWB Localization

- Citation: Henk Wymeersch, Stefano Maranò, Wesley M. Gifford, Moe Z. Win. A Machine Learning Approach to Ranging Error Mitigation for UWB Localization. IEEE Transactions on Communications, 2012. https://doi.org/10.1109/tcomm.2012.042712.110035
- Cluster(s): A, C, D, F
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Location-awareness is becoming increasingly important in wireless networks. Indoor localization can be enabled through wideband or ultra-wide bandwidth (UWB) transmission, due to its fine delay resolution and obstacle-penetration capabilities. A major hurdle is the presence of obstacles that block the line-of-sight (LOS) path between devices, affecting ranging performance and, in turn, localization accuracy. Many techniques have been proposed to address this issue, most of which make modifications to the localization algorithm. Since many localization algorithms work with distance or angle estimates, rather than received waveforms, information inherent in the wideband waveform is lost, leading to sub-optimal ranging error mitigation. To avoid this information loss, we present a novel approach to mitigate ranging errors directly in the...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 18. A Novel NLOS Mitigation Algorithm for UWB Localization in Harsh Indoor Environments

- Citation: Kegen Yu, Kai Wen, Yingbing Li, Shuai Zhang, Kefei Zhang. A Novel NLOS Mitigation Algorithm for UWB Localization in Harsh Indoor Environments. IEEE Transactions on Vehicular Technology, 2018. https://doi.org/10.1109/tvt.2018.2883810
- Cluster(s): C, D, F
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: Non-line-of-sight (NLOS) propagation of radio signals can significantly degrade the performance of ultra-wideband localization systems indoors, it is hence crucial to mitigate the NLOS effect to enhance the accuracy of positioning. The existing NLOS mitigation algorithms to improve localization accuracy are either by compensating range errors through NLOS identification and mitigation methods for ranging or by using dedicated localization techniques. However, they are only applicable to some specific scenarios due to some special assumptions or the need of <italic xmlns:mml="http://www.w3.org/1998/Math/MathML" xmlns:xlink="http://www.w3.org/1999/xlink">a priori</i> knowledge, such as thresholds and distribution functions. Another disadvantage is that they neither have the capability to evaluate the magnitude of NLOS effect nor take...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 19. A Calibration Method for Antenna Delay Estimation and Anchor Self-Localization in UWB Systems

- Citation: Marco Piavanini, Luca Barbieri, Mattia Brambilla, Mattia Cerutti, Simone Ercoli, Andrea Agili, Monica Nicoli. A Calibration Method for Antenna Delay Estimation and Anchor Self-Localization in UWB Systems. 2022 IEEE International Workshop on Metrology for Industry 4.0 & IoT (MetroInd4.0&IoT), 2022. https://doi.org/10.1109/metroind4.0iot54413.2022.9831579
- Cluster(s): A, B
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: This paper addresses the problem of localization through Ultra Wide-Band (UWB) devices in the case of unavailability of a permanent infrastructure, meaning that an adhoc UWB network has to be installed. Deploying an UWB localization system requires human intervention and calibration phases to measure the positions of the anchor nodes. In this paper, we propose an iterative approach based on the Gauss-Newton algorithm for calibration addressing the compensation of the antenna delay at each UWB node and anchors self-localization by two way ranging. The validation considers real experiments in an outdoor scenario, where we show that the proposed compensation procedure can precisely estimate the antenna delays even when few measurements are available, improving the position estimate of UWB anchors and, accordingly, the tag localization...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 20. Improved UWB-based indoor positioning system via NLOS classification and error mitigation

- Citation: Shoude Wang, Nur Syazreen Ahmad. Improved UWB-based indoor positioning system via NLOS classification and error mitigation. Engineering Science and Technology an International Journal, 2025. https://doi.org/10.1016/j.jestch.2025.101979
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Non-Line-of-Sight (NLOS) conditions in indoor positioning systems significantly degrade positioning accuracy. Although Ultra-Wideband (UWB) technology is renowned for its high precision in Line-of-Sight (LOS) environments, under NLOS conditions, positioning errors typically exceed 30 cm. To address this issue, we propose a method for identifying and classifying NLOS signals based on Support Vector Machine Recursive Feature Elimination (SVM-RFE). We extract multiple features from the UWB Channel Impulse Response (CIR) and perform correlation analysis using the Pearson Correlation Coefficient (PCC) to select the most discriminative features via the SVM-RFE algorithm. The classification results are then utilized within an Adaptive Robust Extended Kalman Filter (AREKF) to establish an error model for mitigation. The proposed method was...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 21. Kernel Methods for Accurate UWB-Based Ranging With Reduced Complexity

- Citation: Vladimir Savic, Erik G. Larsson, Javier Ferrer-Coll, Peter Stenumgaard. Kernel Methods for Accurate UWB-Based Ranging With Reduced Complexity. IEEE Transactions on Wireless Communications, 2015. https://doi.org/10.1109/twc.2015.2496584
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Accurate and robust positioning in multipath environments can enable many applications, such as search-and-rescue and asset tracking. For this problem, ultra-wideband (UWB) technology can provide the most accurate range estimates, which are required for range-based positioning. However, UWB still faces a problem with non-line-of-sight (NLOS) measurements, in which the range estimates based on time-of-arrival (TOA) will typically be positively biased. There are many techniques that address this problem, mainly based on NLOS identification and NLOS error mitigation algorithms. However, these techniques do not exploit all available information in the UWB channel impulse response. Kernel-based machine learning methods, such as Gaussian process regression (GPR), are able to make use of all information, but they may be too complex in their...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 22. UWB NLOS/LOS Classification Using Deep Learning Method

- Citation: Changhui Jiang, Jichun Shen, Shuai Chen, Yuwei Chen, Di Liu, Yuming Bo. UWB NLOS/LOS Classification Using Deep Learning Method. IEEE Communications Letters, 2020. https://doi.org/10.1109/lcomm.2020.2999904
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-Wide-Band (UWB) was recognized as its great potential in constructing accurate indoor position system (IPS). However, indoor environments were full of complex objects, the signals might be reflected by the obstacles. Compared with the Line-Of-Sight (LOS) signal, the signal transmitting path delay contained in None-Line-Of-Sight (NLOS) signal would induce positive distance errors and position errors. Before employing ranging information from the channels to calculate the position, LOS/NLOS classification or identification was necessary for selecting the “clean” channels. In conventional method, features extracted from the UWB channel impulse response (CIR) or some other signal properties were employed as the input vector of the machine learning methods, e.g. Support Vector Machine (SVM), Multi-layer Perception (MLP). Deep...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 23. Position error bound for UWB localization in dense cluttered environments

- Citation: Damien Jourdan, Davide Dardari, Moe Z. Win. Position error bound for UWB localization in dense cluttered environments. IEEE Transactions on Aerospace and Electronic Systems, 2008. https://doi.org/10.1109/taes.2008.4560210
- Cluster(s): C, D
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: For most outdoor applications, systems such as global positioning system (GPS) provide users with accurate location estimates. However, similar range-only localization techniques in dense cluttered environments typically lack accuracy and reliability due, notably, to dense multipath, line-of-sight (LOS) blockage and excess propagation delays through materials. In particular, range measurements between a receiver and a transmitter are often positively biased. Furthermore, the quality of the range measurement degrades with distance, and the geometric configuration of the beacons also affects the localization accuracy. In this paper we derive a fundamental limit of localization accuracy for an ultrawide bandwidth (UWB) system operating in such environments, which we call the position error bound (PEB). The impact of different ranging...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 24. Machine Learning Integration in Ultra-Wideband-Based Indoor Positioning Systems: A Comprehensive Review

- Citation: Juan Carlos Santamaria-Pedrón, Rafael Berkvens, Ignacio Miralles, Carlos Reaño, Joaquín Torres-Sospedra. Machine Learning Integration in Ultra-Wideband-Based Indoor Positioning Systems: A Comprehensive Review. Electronics, 2025. https://doi.org/10.3390/electronics15010181
- Cluster(s): A, B, C, D
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-Wideband (UWB) technology enables centimeter-level indoor positioning, but it remains highly sensitive to channel dynamics, multipath and Non-Line-of-Sight (NLoS) propagation. Recent studies increasingly apply Machine Learning (ML) methods to address these issues by modeling nonlinear channel behavior and mitigating ranging bias. This paper presents a comprehensive review and provides a critical synthesis of 169 research works published between 2020 and 2024, offering an integrated overview of how ML techniques are incorporated into UWB-based Indoor Positioning Systems (IPSs). The studies are grouped according to their functional objective, learning algorithm, network architecture, evaluation metrics, dataset, and experimental setting. The results indicate that most approaches apply ML to channel classification and ranging error...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 25. Non-Line-of-Sight Identification and Mitigation Using Received Signal Strength

- Citation: Zhuoling Xiao, Hongkai Wen, Andrew Markham, Niki Trigoni, Phil Blunsom, Jeff Frolík. Non-Line-of-Sight Identification and Mitigation Using Received Signal Strength. IEEE Transactions on Wireless Communications, 2014. https://doi.org/10.1109/twc.2014.2372341
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Indoor wireless systems often operate under non-line-of-sight (NLOS) conditions that can cause ranging errors for location-based applications. As such, these applications could benefit greatly from NLOS identification and mitigation techniques. These techniques have been primarily investigated for ultra-wide band (UWB) systems, but little attention has been paid to WiFi systems, which are far more prevalent in practice. In this study, we address the NLOS identification and mitigation problems using multiple received signal strength (RSS) measurements from WiFi signals. Key to our approach is exploiting several statistical features of the RSS time series, which are shown to be particularly effective. We develop and compare two algorithms based on machine learning and a third based on hypothesis testing to separate LOS/NLOS...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 26. 1D-CLANet: A Novel Network for NLoS Classification in UWB Indoor Positioning System

- Citation: Wang Qiu, Ming-Song Chen, Jiajie Liu, Y.C. Lin, Kai Li, Xin Yan, Chizhou Zhang. 1D-CLANet: A Novel Network for NLoS Classification in UWB Indoor Positioning System. Applied Sciences, 2024. https://doi.org/10.3390/app14177609
- Cluster(s): C, F
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-Wideband (UWB) technology is crucial for indoor localization systems due to its high accuracy and robustness in multipath environments. However, Non-Line-of-Sight (NLoS) conditions can cause UWB signal distortion, significantly reducing positioning accuracy. Thus, distinguishing between NLoS and LoS scenarios and mitigating positioning errors is crucial for enhancing UWB system performance. This research proposes a novel 1D-ConvLSTM-Attention network (1D-CLANet) for extracting UWB temporal channel impulse response (CIR) features and identifying NLoS scenarios. The model combines the convolutional neural network (CNN) and Long Short-Term memory (LSTM) architectures to extract temporal CIR features and introduces the Squeeze-and-Excitation (SE) attention mechanism to enhance critical features. Integrating SE attention with LSTM...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 27. NLOS Error Mitigation for UWB Ranging in Dense Multipath Environments

- Citation: Shaohua Wu, Yongkui Ma, Qinyu Zhang, Naitong Zhang. NLOS Error Mitigation for UWB Ranging in Dense Multipath Environments. 2007 IEEE Wireless Communications and Networking Conference, 2007. https://doi.org/10.1109/wcnc.2007.295
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: To mitigate the non-line-of-sight (NLOS) error of ultra-wideband (UWB) ranging caused by obstructions in dense multipath environments, this paper proposed a novel NLOS error mitigation method. The principles and characteristics of NLOS error are analyzed. Based on the signal propagation path loss model, the NLOS error estimation expression is deduced and further used to calibrate the ranging results. Low complexity path detection algorithms are proposed for implementation of the method. Test on measured data shows that the method can improve the ranging precision greatly.
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 28. Accuracy Assessment and Learned Error Mitigation of UWB ToF Ranging

- Citation: Lorenz Schmid, David Salido-Monzú, Andreas Wieser. Accuracy Assessment and Learned Error Mitigation of UWB ToF Ranging. 2019 International Conference on Indoor Positioning and Indoor Navigation (IPIN), 2019. https://doi.org/10.1109/ipin.2019.8911769
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-wideband (UWB) time of flight (ToF) ranging is nowadays one of the most attractive technologies to implement indoor localization solutions with reliable dm-level accuracy. UWB systems are generally resistant to multipath interference. However, non-line-of-sight (NLOS) components with small relative delays may introduce errors in the estimated distances well exceeding the typically achievable accuracy. We present an empirical accuracy assessment of UWB ranging using a commercial UWB system. A particular focus is on the magnitude and spatial patterns of multipath errors. A large dataset comprising distances between 0.2 and 100 m was collected in a geodetic metrology lab and outdoors for this purpose. We derived ground truth of the distances with superior accuracy using a laser tracker and a total station. The results show that...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 29. Improving UWB Based Indoor Positioning in Industrial Environments Through Machine Learning

- Citation: Sivanand Krishnan, Rochelle Xenia Mendoza Santos, Enhao Ranier Yap, May Thu Zin. Improving UWB Based Indoor Positioning in Industrial Environments Through Machine Learning. 2018 15th International Conference on Control, Automation, Robotics and Vision (ICARCV), 2018. https://doi.org/10.1109/icarcv.2018.8581305
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: The detection and mitigation of Non-Line-of-Sight (NLOS) signals are crucial for achieving the full potential of UWB-based indoor positioning. In dense multipath industrial environments, it was seen that using the power characteristics of the received signal to identify NLOS conditions is effective when tracking stationary objects but is insufficient for mobile object tracking. Hence, machine learning classifiers utilizing Multi-Layer Perceptron (MLP) and Boosted Decision Trees (BDT) were developed to improve NLOS detection. Through experimental results from tests in a factory scenario, it is shown that BDT yields a higher accuracy of 87% as compared to the 79% obtained by the received power based method.
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 30. NLOS identification and compensation for UWB ranging based on obstruction classification

- Citation: Kai Wen, Kegen Yu, Yingbing Li. NLOS identification and compensation for UWB ranging based on obstruction classification. 2017 25th European Signal Processing Conference (EUSIPCO), 2017. https://doi.org/10.23919/eusipco.2017.8081702
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Non-line-of-sight (NLOS) propagation is one of the major barriers to accurate ranging and positioning based on time of arrival (TOA) in the application of an ultra wideband (UWB) system. This paper proposes a new method for NLOS identification and mitigation based on signal characteristic analysis and fuzzy theory. This method neither requires to build a statistical model nor to create and update a training database, so that it can be used conveniently for different application scenarios. Extensive experiments were conducted and the results show that the cumulative distribution function of the ranging error below 0.5 meter is over 90% when using the proposed mitigation method, while that without using the mitigation method is below 70%. Also, by using the proposed method, the root mean square error (RMSE) of the range measurements is...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 31. Multi-Classification of UWB Signal Propagation Channels Based on One-Dimensional Wavelet Packet Analysis and CNN

- Citation: Jin Wang, Kegen Yu, Jinwei Bu, Yiruo Lin, Shuai Han. Multi-Classification of UWB Signal Propagation Channels Based on One-Dimensional Wavelet Packet Analysis and CNN. IEEE Transactions on Vehicular Technology, 2022. https://doi.org/10.1109/tvt.2022.3172863
- Cluster(s): C, D
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Due to the strong penetration ability and high transmission rate of ultra-wideband (UWB) signals, UWB technology plays a very significant role in the field of precise indoor positioning. However, the harsh and volatile indoor environment leads to non-line-of-sight (NLOS) propagation and severe attenuation of UWB signals, which may generate significant ranging and positioning errors. To mitigate NLOS effect and improve positioning accuracy, existing methods use ranging information and channel impulse response (CIR) to identify UWB signal propagation channels. However, these NLOS identification methods often require a priori knowledge and suitable thresholds, and most of them only perform a binary classification between LOS and NLOS in a particular scenario. To address these disadvantages, this paper proposes a novel...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 32. NLOS Classification Based on RSS and Ranging Statistics Obtained from Low-Cost UWB Devices

- Citation: Valentín Barral, Carlos J. Escudero, José A. García‐Naya. NLOS Classification Based on RSS and Ranging Statistics Obtained from Low-Cost UWB Devices. 2019 27th European Signal Processing Conference (EUSIPCO), 2019. https://doi.org/10.23919/eusipco.2019.8902949
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-wideband (UWB) devices have been largely considered for indoor location systems due to their high accuracy. However, as in other wireless systems, such accuracy is significantly degraded under non-line-of-sight (NLOS) propagation conditions. Therefore, the identification of NLOS conditions is essential to mitigate inaccuracies due to NLOS propagation. Nonetheless, most of the techniques considered to identify NLOS situations are based on the study of the channel impulse response (CIR), which is not practical and even becomes unfeasible when employing low-cost UWB hardware. This is precisely the main motivation of this work, to introduce a classification system based on the statistics of both the received signal strength (RSS) and range available from low-cost UWB devices. We analyze the effect of considering different statistic...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 33. Entropy-Based TOA Estimation and SVM-Based Ranging Error Mitigation in UWB Ranging Systems

- Citation: Zhendong Yin, Kai Cui, Zhilu Wu, Liang Yin. Entropy-Based TOA Estimation and SVM-Based Ranging Error Mitigation in UWB Ranging Systems. Sensors, 2015. https://doi.org/10.3390/s150511701
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: The major challenges for Ultra-wide Band (UWB) indoor ranging systems are the dense multipath and non-line-of-sight (NLOS) problems of the indoor environment. To precisely estimate the time of arrival (TOA) of the first path (FP) in such a poor environment, a novel approach of entropy-based TOA estimation and support vector machine (SVM) regression-based ranging error mitigation is proposed in this paper. The proposed method can estimate the TOA precisely by measuring the randomness of the received signals and mitigate the ranging error without the recognition of the channel conditions. The entropy is used to measure the randomness of the received signals and the FP can be determined by the decision of the sample which is followed by a great entropy decrease. The SVM regression is employed to perform the ranging-error mitigation by...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 34. Transfer Learning for UWB Error Correction and (N)LOS Classification in Multiple Environments

- Citation: Jaron Fontaine, Fuhu Che, Adnan Shahid, Ben Van Herbruggen, Qasim Zeeshan Ahmed, Waqas Bin Abbas, Eli De Poorter. Transfer Learning for UWB Error Correction and (N)LOS Classification in Multiple Environments. IEEE Internet of Things Journal, 2023. https://doi.org/10.1109/jiot.2023.3299319
- Cluster(s): C, D
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: Ultra wideband (UWB) is a popular technology to address the need for high-precision indoor positioning systems in challenging industry 4.0 use cases. In line-of-sight (LOS) environments, UWB positioning errors in the order of 1–10 cm can be achieved. However, in non-line-of-sight (NLOS) conditions, this precision drops significantly, with errors typically >30 cm. Machine learning (ML) has been proposed to improve the precision in such NLOS conditions, but is typically environment-specific and lacks generalization to new environments and UWB configurations. As such, it is necessary to collect large data sets to train a neural network (NN) for each new environment or UWB configuration. To remedy this, this article proposes automatic optimizations for transfer learning (TL) deep NNs toward new environments and UWB configurations. We...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 35. NLOS Identification for UWB Based on Channel Impulse Response

- Citation: Zhuoqi Zeng, Steven Liu, Lei Wang. NLOS Identification for UWB Based on Channel Impulse Response. 2018 12th International Conference on Signal Processing and Communication Systems (ICSPCS), 2018. https://doi.org/10.1109/icspcs.2018.8631718
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: The localization accuracy of ultra-wide band (UWB) system could be dramatically degraded, if the signal is propagated under non-line-of-sight (NLOS) condition. The detection of the NLOS propagation is very important to guarantee the accuracy of the UWB system. Based on the channel impulse response (CIR) sample, the NLOS condition could be identified. However, for the decawave chips, each CIR sample contains 1015 points. Thus the real-time realization of the NLOS detection with CIR is very hard, since the import and calculation of such a large amount of data cause to huge delay. In order to reduce the delay, the minimal needed size of the points in CIR for accurate NLOS identification is discussed in this paper. The support vector machine (SVM) is used for the classification based on the original CIR points or the eight different...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 36. UWB NLOS Identification and Mitigation Based on Gramian Angular Field and Parallel Deep Learning Model

- Citation: Bowen Deng, T. Xu, Maode Yan. UWB NLOS Identification and Mitigation Based on Gramian Angular Field and Parallel Deep Learning Model. IEEE Sensors Journal, 2023. https://doi.org/10.1109/jsen.2023.3323564
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultrawideband (UWB) wireless localization technology has been widely applied in the field of indoor localization due to its good ability of noise resistance, strong penetration, and high measurement accuracy. However, the performance of UWB-based localization technology becomes poor when suffering from nonline-of-sight (NLOS) propagation conditions. Thus, it is necessary to identify NLOS propagation and mitigate the NLOS error. In this article, a novel NLOS identification and mitigation method based on multiinputs parallel deep learning model and Gramian angular field (GAF) is proposed. We utilize GAF to transform 1-D channel impulse response (CIR) signal into 2-D colored images, which adds additional high-level abstract features to the CIR signals. In the model training phase, the convolutional neural network (CNN) is used to extract...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 37. Performance Comparison of WiFi and UWB Fingerprinting Indoor Positioning Systems

- Citation: Giuseppe Caso, Mai T. P. Le, Luca De Nardis, Maria‐Gabriella Di Benedetto. Performance Comparison of WiFi and UWB Fingerprinting Indoor Positioning Systems. Technologies, 2018. https://doi.org/10.3390/technologies6010014
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Ultra-wideband (UWB) and WiFi technologies have been widely proposed for the implementation of accurate and scalable indoor positioning systems (IPSs). Among different approaches, fingerprinting appears particularly suitable for WiFi IPSs and was also proposed for UWB IPSs, in order to cope with the decrease in accuracy of time of arrival (ToA)-based lateration schemes in the case of severe multipath and non-line-of-sight (NLoS) environments. However, so far, the two technologies have been analyzed under very different assumptions, and no fair performance comparison has been carried out. This paper fills this gap by comparing UWB- and WiFi-based fingerprinting under similar settings and scenarios by computer simulations. Two different k-nearest neighbor (kNN) algorithms are considered in the comparison: a traditional fixed k...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 38. UWB-Based Localization System Aided With Inertial Sensor for Underground Coal Mine Applications

- Citation: Menggang Li, Hua Zhu, Shaoze You, Chaoquan Tang. UWB-Based Localization System Aided With Inertial Sensor for Underground Coal Mine Applications. IEEE Sensors Journal, 2020. https://doi.org/10.1109/jsen.2020.2976097
- Cluster(s): A, F
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Robotic mining equipment plays an increasingly important role in the coal mining industry. Due to the complexity of the confined underground environment, available localization methods are limited, and restrict the development of coal mine robots (CMRs). Ultra-wideband (UWB) is a promising positioning sensor with high ranging accuracy. However, current applications about UWB positioning in coal mine focus mainly on position information, but rarely on orientation information. Positioning accuracy is often plagued by the loss of transmitted signals and multipath effects. In this paper, a pseudo-GPS positioning system in underground coal mine, composed by noisy UWB range measurements, is proposed to provide localization service for CMRs. An Error-State Kalman Filter (ESKF) is used for fusing measurements from the inertial measurement...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 39. Real Time Indoor Positioning System for Smart Grid based on UWB and Artificial Intelligence Techniques

- Citation: Long Cheng, Hao Chang, Kexin Wang, Zhaoqi Wu. Real Time Indoor Positioning System for Smart Grid based on UWB and Artificial Intelligence Techniques. 2020 IEEE Conference on Technologies for Sustainability (SusTech), 2020. https://doi.org/10.1109/sustech47890.2020.9150486
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: robust estimation or outlier mitigation.
- Abstract/key finding read: Indoor positioning system plays an important role in smart grid. Although GPS is the predominant outdoor positioning technology, it is unsuitable to be used in many fields of smart grid for three main reasons: first, signals sent from GPS could easily get blocked by solid materials such as metal or brick; second, the complex electromagnetic interference induced by electrical circuits greatly affects GPS signals; third, GPS can only achieve meter-level real time positioning accuracy, which is far from sufficient for many requirements of smart grid applications. Some other indoor positioning technologies, such as Bluetooth, Wi-Fi, ultrasound, infrared and RFID, fail in either the positioning accuracy, the positioning range, or the positioning speed required in many smart grid applications. Therefore, this paper proposes a real time...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 40. NLOS Identification and Mitigation for Time-based Indoor Localization Systems: Survey and Future Research Directions

- Citation: Raphael E. Nkrow, Bruno Silva, Dutliff Boshoff, Gerhard P. Hancke, Mikael Gidlund, Adnan M. Abu‐Mahfouz. NLOS Identification and Mitigation for Time-based Indoor Localization Systems: Survey and Future Research Directions. ACM Computing Surveys, 2024. https://doi.org/10.1145/3663473
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: One hurdle to accurate indoor localization using time-based networks is the presence of Non-Line-Of-Sight (NLOS) and multipath signals, affecting the accuracy of ranging in indoor environments. NLOS identification and mitigation have been studied over the years and applied to different time-based networks, with most works considering NLOS links with WiFi and UWB channels. In this article, we discuss the effects and challenges of NLOS conditions on indoor localization and present current state-of-the-art approaches to NLOS identification and mitigation in literature. We survey these approaches and classify them under different categories together with their merits and demerits. We further categorize approaches to tackle NLOS effects into single and hybrid measurement-based approaches in this work. Lessons learnt from the survey with...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 41. Static and Dynamic Evaluation of an UWB Localization System for Industrial Applications

- Citation: Mickaël Delamare, Rémi Boutteau, Xavier Savatier, Nicolas Iriart. Static and Dynamic Evaluation of an UWB Localization System for Industrial Applications. Sci, 2020. https://doi.org/10.3390/sci2020023
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Many applications in the context of Industry 4.0 require precise localization. However, indoor localization remains an open problem, especially in complex environments such as industrial environments. In recent years, we have seen the emergence of Ultra WideBand (UWB) localization systems. The aim of this article is to evaluate the performance of a UWB system to estimate the position of a person moving in an indoor environment. To do so, we implemented an experimental protocol to evaluate the accuracy of the UWB system both statically and dynamically. The UWB system is compared to a ground truth obtained by a motion capture system with a millimetric accuracy.
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 42. UWB Positioning System Based on LSTM Classification With Mitigated NLOS Effects

- Citation: Daeho Kim, Arshad Farhad, Jae-Young Pyun. UWB Positioning System Based on LSTM Classification With Mitigated NLOS Effects. IEEE Internet of Things Journal, 2022. https://doi.org/10.1109/jiot.2022.3209735
- Cluster(s): C, D
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: It is known that an ultrawideband (UWB)-based indoor positioning system (IPS) has superior positioning performance and can meet the requirements of location-based services (LBSs) as the Internet of Things (IoT) applications. However, there is a limitation of UWB positioning when it is conducted at the nonline-of-sight (NLOS) channels degrading the UWB ranging accuracy at indoor environments. In this article, we propose an artificial intelligence (AI) applied UWB positioning system that can enhance the positioning performance by classifying channel conditions with channel impulse response (CIR) of the received UWB signal. The proposed system mitigates the positioning degradation caused by the NLOS situations by performing extended Kalman filter (EKF) localization and long short-term memory (LSTM) training of the observed channel...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 43. Robust LOS/NLOS Identification for UWB Signals Using Improved Fuzzy Decision Tree Under Volatile Indoor Conditions

- Citation: Feiyang Zhu, Kegen Yu, Yiruo Lin, Changyang Wang, Jin Wang, Minghua Chao. Robust LOS/NLOS Identification for UWB Signals Using Improved Fuzzy Decision Tree Under Volatile Indoor Conditions. IEEE Transactions on Instrumentation and Measurement, 2023. https://doi.org/10.1109/tim.2023.3276521
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: Ultra-wideband (UWB) is a very promising indoor wireless positioning technology. However, in the harsh and volatile indoor environment, the propagation of UWB signals is vulnerable to non-line-of-sight (NLOS) conditions, and the contaminated range measurements will degrade the accuracy for UWB localization. Therefore, it is necessary to identify LOS/NLOS. Recent studies mainly focus on the identification of UWB signal propagation conditions by using channel impulse response (CIR) or extracted channel statistical features. However, these studies usually only focus on specific indoor environment or stable indoor conditions. In fact, the indoor environment is harsh and changeable. In order to deal with the dynamic and uncertain information of the indoor environments, this paper proposes a robust method to identify LOS/NLOS using fuzzy...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 44. NLOS Identification and Mitigation Using Low-Cost UWB Devices

- Citation: Valentín Barral, Carlos J. Escudero, José A. García‐Naya, Roberto Maneiro-Catoira. NLOS Identification and Mitigation Using Low-Cost UWB Devices. Sensors, 2019. https://doi.org/10.3390/s19163464
- Cluster(s): C, F
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Indoor location systems based on ultra-wideband (UWB) technology have become very popular in recent years following the introduction of a number of low-cost devices on the market capable of providing accurate distance measurements. Although promising, UWB devices also suffer from the classic problems found when working in indoor scenarios, especially when there is no a clear line-of-sight (LOS) between the emitter and the receiver, causing the estimation error to increase up to several meters. In this work, machine learning (ML) techniques are employed to analyze several sets of real UWB measurements, captured in different scenarios, to try to identify the measurements facing non-line-of-sight (NLOS) propagation condition. Additionally, an ulterior process is carried out to mitigate the deviation of these measurements from the actual...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 45. UWB NLOS Identification and Mitigation based on Bidirectional Encoder Representations from Transformer (BERT) Deep Learning

- Citation: Hongchao Yang, Yunjia Wang, Cheekiat Seow, Meng Sun, David Plets. UWB NLOS Identification and Mitigation based on Bidirectional Encoder Representations from Transformer (BERT) Deep Learning. 2024 14th International Conference on Indoor Positioning and Indoor Navigation (IPIN), 2024. https://doi.org/10.1109/ipin62893.2024.10786116
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: The Non-Line-of-Sight (NLOS) phenomenon can hinder signal propagation and significantly reduce the accuracy of UWB for indoor positioning and navigation. The Channel Impulse Response (CIR) sequence generated during UWB ranging is widely used for channel identification. However, existing deep learning algorithms struggle to balance the local and global features of the CIR sequence effectively. To address this, this paper constructs a Bidirectional Encoder Representations from Transformers (BERT) channel identification model using the self-attention mechanism to improve the NLOS identification. The identification Accuracy, LOS recall, and F2 scores in multiple scenarios are 96.65%, 97.13%, and 0.9703 respectively. Comparing to state-of-art algorithms such as LS-SVM, CNN, and LSTM, our algorithm outperformed by 17.9%, 11.86%, and 10.80%...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 46. Parallel Deep Learning for NLOS Detection and Error Mitigation in UWB Positioning

- Citation: Qiu Wang, Ming-Song Chen, J F Liu, Z. Li, Xin Yan, Y.C. Lin, Kai Li, Chizhou Zhang. Parallel Deep Learning for NLOS Detection and Error Mitigation in UWB Positioning. IEEE Internet of Things Journal, 2025. https://doi.org/10.1109/jiot.2025.3597300
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-wideband (UWB) technology is extensively applied in indoor high-precision localization scenarios. However, barriers along the radio signal path can lead to Non-Line-of-Sight (NLOS) propagation, thereby reducing positioning reliability. Therefore, identifying NLOS conditions and mitigating the associated errors is essential. In this paper, a novel NLOS detection and mitigation approach is introduced, leveraging the parallel Spatio-Temporal feature fusion network (PSTFFN) and Spatio-Temporal attention regression network (STARN). We utilize continuous wavelet transform to convert the one-dimensional channel impulse response (CIR) signal into a two-dimensional time-frequency diagram of CIR image data. By incorporating an attention mechanism and handcrafted features, PSTFFN enhances its ability to differentiate between scenarios with...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 47. A Novel Self-Calibrated UWB-Based Indoor Localization Systems for Context-Aware Applications

- Citation: Tanveer Ahmad, Muhammad Usman, Marryam Murtaza, Ian B. Benitez, Asim Anwar, Vasos Vassiliou, Azeem Irshad, Xue Jun Li, et al.. A Novel Self-Calibrated UWB-Based Indoor Localization Systems for Context-Aware Applications. IEEE Transactions on Consumer Electronics, 2024. https://doi.org/10.1109/tce.2024.3369193
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Location information is the most crucial information used in context-aware applications, e-commerce and IoT-based consumer applications. Traditional methods doesn’t focus on network coverage, accuracy, hardware cost, and noise in dense environment. To defeat these issues, this paper presents a novel localization algorithm for UWB nodes adopting self-calibration and ToA measurement for context-aware applications. The Link quality induction values are used instead of RSSI for distance estimation by costing technique. A calibration factor (CF) is further introduce to automatically update the location information in mobility. As the signal strength can be distorted heavily due to shadowing and multi-path fading, the localization is estimated in noisy condition and extended Kalman filtering (EKF) is applied to refine the node coordinates....
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 48. Hybrid Quantum Convolutional Neural Networks for UWB Signal Classification

- Citation: Seon-Geun Jeong, Quang-Vinh Do, Hae-Ji Hwang, Mikio Hasegawa, Hiroo Sekiya, Won–Joo Hwang. Hybrid Quantum Convolutional Neural Networks for UWB Signal Classification. IEEE Access, 2023. https://doi.org/10.1109/access.2023.3323019
- Cluster(s): C, D
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: With the increasing requirements for location-based services for Internet of things (IoT) applications, ultrawideband (UWB) technology provides accurate indoor positioning capabilities. However, indoor environments contain various obstacles leading to significant signal propagation effects. This results in errors in the time-of-arrival-based UWB positioning system. Specifically, a non-line-of-sight (NLOS) signal induces additional distance and position errors owing to the path delay compared to a line-of-sight (LOS) signal. Therefore, UWB signal classification is essential for improving positioning accuracy. Recently, various approaches have successfully classified UWB signals, including machine-learning-based methods such as convolutional neural networks (CNNs) and long short-term memory (LSTM). This study proposes a hybrid quantum...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 49. NLOS Mitigation for UWB Localization Based on Sparse Pseudo-Input Gaussian Process

- Citation: Xiaofeng Yang. NLOS Mitigation for UWB Localization Based on Sparse Pseudo-Input Gaussian Process. IEEE Sensors Journal, 2018. https://doi.org/10.1109/jsen.2018.2818158
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-wideband technology has found promising application in high accuracy localization due to its high time resolution and through-wall propagation properties. However, its performance seriously degrades in non-line-of-sight (NLOS) scenario. Gaussian Process (GP) regression is the state-of-the-art machine learning approach that addresses this issue. But it is too complex in its original form. This paper proposes a novel NLOS mitigation method based on Sparse Pseudo-input Gaussian Process (SPGP) with low complexity. In contrast to conventional approaches which perform NLOS identification first, this approach directly mitigates the bias of both LOS and NLOS conditions. Monte-Carlo simulations demonstrate that with much less (very sparse) training data, SPGP achieves performance comparable to GP regression.
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 50. Empirical Based Ranging Error Mitigation in IR-UWB: A Fuzzy Approach

- Citation: Sunil Kumar Meghani, Muhammad Asif, Faroq Awin, Kemal Tepe. Empirical Based Ranging Error Mitigation in IR-UWB: A Fuzzy Approach. IEEE Access, 2019. https://doi.org/10.1109/access.2019.2904201
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Indoor tracking and navigation (ITN) mainly depend on indoor localization. An impulse radio ultra-wideband (IR-UWB) is the most advanced technology for precision indoor localization. Besides its precision, the IR-UWB also has low complex hardware, low power consumption, and a flexible data rate that makes it the ideal candidate for ITN. However, two significant challenges impede the achievement of high-resolution accuracy and optimum performance: non-line-of-sight (NLOS) channel condition and multipath propagation (MPP). To enhance the performance under these conditions, the ranging error is estimated and corrected using parameters' uncertainties. The uncertainties in the channel's parameters have a relationship with the error, and these uncertainties are induced due to the NLOS and MPP propagation conditions. The parameters are...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: mentions identifiability/observability/Fisher/CRB-style terms.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 51. Identification of NLOS and Multi-Path Conditions in UWB Localization Using Machine Learning Methods

- Citation: Cung Lian Sang, Bastian Steinhagen, Jonas Dominik Homburg, Michael Adams, Marc Hesse, Ulrich Rückert. Identification of NLOS and Multi-Path Conditions in UWB Localization Using Machine Learning Methods. Applied Sciences, 2020. https://doi.org/10.3390/app10113980
- Cluster(s): C, D
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: In ultra-wideband (UWB)-based wireless ranging or distance measurement, differentiation between line-of-sight (LOS), non-line-of-sight (NLOS), and multi-path (MP) conditions is important for precise indoor localization. This is because the accuracy of the reported measured distance in UWB ranging systems is directly affected by the measurement conditions (LOS, NLOS, or MP). However, the major contributions in the literature only address the binary classification between LOS and NLOS in UWB ranging systems. The MP condition is usually ignored. In fact, the MP condition also has a significant impact on the ranging errors of the UWB compared to the direct LOS measurement results. However, the magnitudes of the error contained in MP conditions are generally lower than completely blocked NLOS scenarios. This paper addresses machine...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 52. UWB-based System for UAV Localization in GNSS-Denied Environments: Characterization and Dataset

- Citation: Jorge Peña Queralta, Carmen Martínez Almansa, Fabrizio Schiano, Dario Floreano, Tomi Westerlund. UWB-based System for UAV Localization in GNSS-Denied Environments: Characterization and Dataset. 2020 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS), 2020. https://doi.org/10.1109/iros45743.2020.9341042
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: robust estimation or outlier mitigation.
- Abstract/key finding read: Small unmanned aerial vehicles (UAV) have penetrated multiple domains over the past years. In GNSS-denied or indoor environments, aerial robots require a robust and stable localization system, often with external feedback, in order to fly safely. Motion capture systems are typically utilized indoors when accurate localization is needed. However, these systems are expensive and most require a fixed setup. In this paper, we study and characterize an ultra-wideband (UWB) system for navigation and localization of aerial robots indoors based on Decawave's DWM1001 UWB node. The system is portable, inexpensive and can be battery powered in its totality. We show the viability of this system for autonomous flight of UAVs, and provide open-source methods and data that enable its widespread application even with movable anchor systems. We...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 53. Toward Standard Non-Line-of-Sight Benchmarking of Ultra-Wideband Radio-Based Localization

- Citation: Milad Heydariaan, Hessam Mohammadmoradi, Omprakash Gnawali. Toward Standard Non-Line-of-Sight Benchmarking of Ultra-Wideband Radio-Based Localization. 2018 IEEE Workshop on Benchmarking Cyber-Physical Networks and Systems (CPSBench), 2018. https://doi.org/10.1109/cpsbench.2018.00010
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Performance of Ultra-wideband (UWB) radios in non-line-of-sight (NLoS) environments has been a topic of interest among researchers, especially when it comes to indoor localization applications. It is known that NLoS propagation of electromagnetic waves can severely affect the localization accuracy. Despite the interest in indoor localization performance, it is still difficult to compare results from different studies without proper evaluation standards. Understanding the types of materials used in a testing environment could be a proper technique for benchmarking different localization solutions in different scenarios. We provide a systematic study to investigate effects of signal refraction and attenuation on UWB signals in different construction materials by examining the Channel Impulse Response (CIR) and ranging accuracy. Further,...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 54. A Method for UWB Localization Based on CNN-SVM and Hybrid Locating Algorithm

- Citation: Zefu Gao, Yiwen Jiao, Wenge Yang, Xuejian Li, Yuxin Wang. A Method for UWB Localization Based on CNN-SVM and Hybrid Locating Algorithm. Information, 2023. https://doi.org/10.3390/info14010046
- Cluster(s): C, F
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: In this paper, aiming at the severe problems of UWB positioning in NLOS-interference circumstances, a complete method is proposed for NLOS/LOS classification, NLOS identification and mitigation, and a final accurate UWB coordinate solution through the integration of two machine learning algorithms and a hybrid localization algorithm, which is called the C-T-CNN-SVM algorithm. This algorithm consists of three basic processes: an LOS/NLOS signal classification method based on SVM, an NLOS signal recognition and error elimination method based on CNN, and an accurate coordinate solution based on the hybrid weighting of the Chan–Taylor method. Finally, the validity and accuracy of the C-T-CNN-SVM algorithm are proved through a comparison with traditional and state-of-the-art methods. (i) Focusing on four main prediction errors (range...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 55. Static and Dynamic Evaluation of an UWB Localization System for Industrial Applications

- Citation: Mickaël Delamare, Rémi Boutteau, Xavier Savatier, Nicolas Iriart. Static and Dynamic Evaluation of an UWB Localization System for Industrial Applications. Sci, 2019. https://doi.org/10.3390/sci1030062
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Many applications in the context of Industry 4.0 require precise localization. However, indoor localization remains an open problem, especially in complex environments such as industrial environments. In recent years, we have seen the emergence of Ultra WideBand (UWB) localization systems. The aim of this article is to evaluate the performance of a UWB system to estimate the position of a person moving in an indoor environment. To do so, we implemented an experimental protocol to evaluate the accuracy of the UWB system both statically and dynamically. The UWB system is compared to a ground truth obtained by a motion capture system with a millimetric accuracy.
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 56. NLOS Identification for UWB Positioning Based on IDBO and Convolutional Neural Networks

- Citation: Qiankun Kong. NLOS Identification for UWB Positioning Based on IDBO and Convolutional Neural Networks. IEEE Access, 2023. https://doi.org/10.1109/access.2023.3344640
- Cluster(s): A, C, D
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Ultra-wideband (UWB) is regarded as the technology with the most potential for precise indoor location due to its centimeter-level ranging capabilities, good time resolution, and low power consumption. However, Because of the presence of non-line-of-sight (NLOS) error, the accuracy of UWB localization deteriorates significantly in harsh and volatile indoor conditions. Therefore, identifying NLOS conditions is crucial to enhancing the accuracy of UWB location. This paper proposes a convolutional neural network (CNN) classification method based on an improved Dung Beetle Optimizer (DBO). Firstly, based on the standard DBO, the Circle chaotic mapping, non-uniform Gaussian variational strategy, and multi-stage perturbation strategy are used to optimize the exploration capability and enhance the performance of original DBO method, the...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 57. A New Calibration Method of UWB Antenna Delay Based on the ADS-TWR

- Citation: Xinzhe Gui, Shuli Guo, Qiming Chen, Lina Han. A New Calibration Method of UWB Antenna Delay Based on the ADS-TWR. 2018 37th Chinese Control Conference (CCC), 2018. https://doi.org/10.23919/chicc.2018.8483104
- Cluster(s): B
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: This paper presents a new Ultra-wideband (UWB) antenna delay calibration method which is based on the Asymmetric Double-sided Two-way Ranging (ADS-TWR) to get the precise information of distance measurement with different UWB devices. We use a similar derivation as ADS-TWR to build the objective function based on the antenna delay model. Then we use Particle Swarm Optimization (PSO) to optimize the objective function and get the independent antenna delay of each device according to combination of different devices antenna delay. Finally we use the least square positioning method to verify the performance of different calibration methods. The result shows that our method is effective to the distance measurement system and positioning system. Compared with rough calibration method and official calibration method, our proposed method...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 58. Accurate Indoor Positioning for UWB-Based Personal Devices Using Deep Learning

- Citation: Sangmo Sung, Hokeun Kim, Jae-il Jung. Accurate Indoor Positioning for UWB-Based Personal Devices Using Deep Learning. IEEE Access, 2023. https://doi.org/10.1109/access.2023.3250180
- Cluster(s): C, D, F
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Recently, there has been a rapidly emerging demand for localization technologies to provide various location-based services in indoor environments, such as smart buildings, smart factories, and parking lots, as well as outdoor environments. Ultra-wideband (UWB), an emerging wireless technology, provides opportunities for precise indoor positioning with sub-meter accuracy, much more accurate than WiFi or BLE-based techniques, thanks to its signal and communication characteristics. UWB technology has recently begun to be applied to personal devices such as smartphones and is expected to be used for indoor localization of personal devices. However, personal devices often cause signal problems because they are worn on human hands or bodies and move dynamically or are in a non-line-of-sight (NLoS) condition, such as pockets or bags....
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 59. WUB-IP: A High-Precision UWB Positioning Scheme for Indoor Multiuser Applications

- Citation: Zhendong Yin, Xu Jiang, Zhutian Yang, Nan Zhao, Yunfei Chen. WUB-IP: A High-Precision UWB Positioning Scheme for Indoor Multiuser Applications. IEEE Systems Journal, 2017. https://doi.org/10.1109/jsyst.2017.2766690
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: High-precision positioning scheme, an important part of indoor navigation, can be implemented using an ultra-wideband (UWB) based ranging system. Recently, solutions for precise positioning in dense multipath and non-line-of-sight (NLOS) conditions have attracted a lot of attentions. On the other hand, it is expected that waveform division multiple access (WDMA) technology for multiuser UWB positioning applications will be indispensable in the near future. In this regard, a WDMA-UWB-based positioning scheme is investigated in this paper, to enhance the positioning accuracy in multiuser applications. In accordance with practical requirements of indoor positioning, we propose a new indoor positioning scheme, termed as WDMA-UWB-based indoor positioning (WUB-IP). This scheme adopts WDMA for multiple access, and utilizes an entropy-based...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 60. Anchor self-localization algorithm based on UWB ranging and inertial measurements

- Citation: Qin Shi, Sihao Zhao, Xiaowei Cui, Mingquan Lu, Mengdi Jia. Anchor self-localization algorithm based on UWB ranging and inertial measurements. Tsinghua Science & Technology, 2019. https://doi.org/10.26599/tst.2018.9010102
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Localization systems utilizing Ultra-WideBand (UWB) have been widely used in dense urban and indoor environments. A moving UWB tag can be located by ranging to fixed UWB anchors whose positions are surveyed in advance. However, manually surveying the anchors is typically a dull and time-consuming process and prone to artificial errors. In this paper, we present an accurate and easy-to-use method for UWB anchor self-localization, using the UWB ranging measurements and readings from a low-cost Inertial Measurement Unit (IMU). The locations of the anchors are automatically estimated by freely moving the tag in the environment. The method is inspired by the Simultaneous Localization And Mapping (SLAM) technique used by the robotics community. A tightly-coupled Error-State Kalman Filter (ESKF) is utilized to fuse UWB and inertial...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 61. An UWB Channel Impulse Response De-Noising Method for NLOS/LOS Classification Boosting

- Citation: Changhui Jiang, Shuai Chen, Yuwei Chen, Di Liu, Yuming Bo. An UWB Channel Impulse Response De-Noising Method for NLOS/LOS Classification Boosting. IEEE Communications Letters, 2020. https://doi.org/10.1109/lcomm.2020.3009659
- Cluster(s): C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: With the booming demand of indoor position information, Wireless signals (Wi-Fi, Bluetooth, Ultra-Wide-Band etc.) are investigated to construct Indoor Positioning System (IPS). Among these wireless signals, UWB (Ultra-Wide-Band) is recognized as the most promising technology to construct IPS with decimeter-level positioning accuracy. However, there are various objects in indoor environments, UWB signals might be reflected by these surrounding objects. These None-Line-Of-Sight (NLOS) signals will induce additional errors to the distance measurements between the anchor and agent. Therefore, NLOS/LOS (Line-Of-Sight) signals classification should be carried out for identifying the NLOS reception. In UWB based IPS, the distance information is extracted through the Channel Impulse Response (CIR) waveforms. NLOS reception will lead to...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 62. Multihop Self-Calibration Algorithm for Ultra-Wideband (UWB) Anchor Node Positioning

- Citation: Ben Van Herbruggen, Stijn Luchie, Jaron Fontaine, Eli De Poorter. Multihop Self-Calibration Algorithm for Ultra-Wideband (UWB) Anchor Node Positioning. IEEE Journal of Indoor and Seamless Positioning and Navigation, 2023. https://doi.org/10.1109/jispin.2023.3276826
- Cluster(s): A, D
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: Ultra-wideband (UWB) is an emerging technology for indoor localization systems with high accuracy and excellent resilience against multipath fading and interference from other technologies. However, UWB localization systems require the installation of infrastructure devices (anchor nodes) with known positions to serve as reference points. These coordinates are of utmost importance for the performance of the indoor localization system as the position of the mobile tag(s) will be calculated based on this information. Currently most large-scale systems require manual measurement of the anchor coordinates, which is a time-consuming and error-prone process. Therefore, we propose an algorithmic approach whereby based on measurements of the position of a small random chosen subset of anchors, the position of all other anchors is calculated...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 63. An Approach to Robust INS/UWB Integrated Positioning for Autonomous Indoor Mobile Robots

- Citation: Jianfeng Liu, Jiexin Pu, Lifan Sun, Zishu He. An Approach to Robust INS/UWB Integrated Positioning for Autonomous Indoor Mobile Robots. Sensors, 2019. https://doi.org/10.3390/s19040950
- Cluster(s): C, D, F
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: The key to successful positioning of autonomous mobile robots in complicated indoor environments lies in the strong anti-interference of the positioning system and accurate measurements from sensors. Inertial navigation systems (INS) are widely used for indoor mobile robots because they are not susceptible to external interferences and work properly, but the positioning errors may be accumulated over time. Thus ultra wideband (UWB) is usually adopted to compensate the accumulated errors due to its high ranging precision. Unfortunately, UWB is easily affected by the multipath effects and non-line-of-sight (NLOS) factor in complex indoor environments, which may degrade the positioning performance. To solve above problems, this paper proposes an effective system framework of INS/UWB integrated positioning for autonomous indoor mobile...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 64. Calibration and Uncertainty Characterization for Ultra-Wideband Two-Way-Ranging Measurements

- Citation: Mohammed Shalaby, Charles Champagne Cossette, James Richard Forbes, Jérôme Le Ny. Calibration and Uncertainty Characterization for Ultra-Wideband Two-Way-Ranging Measurements. arXiv (Cornell University), 2022. https://doi.org/10.48550/arxiv.2210.05888
- Cluster(s): B, F
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: Ultra-Wideband (UWB) systems are becoming increasingly popular for indoor localization, where range measurements are obtained by measuring the time-of-flight of radio signals. However, the range measurements typically suffer from a systematic error or bias that must be corrected for high-accuracy localization. In this paper, a ranging protocol is proposed alongside a robust and scalable antenna-delay calibration procedure to accurately and efficiently calibrate antenna delays for many UWB tags. Additionally, the bias and uncertainty of the measurements are modelled as a function of the received-signal power. The full calibration procedure is presented using experimental training data of 3 aerial robots fitted with 2 UWB tags each, and then evaluated on 2 test experiments. A localization problem is then formulated on the experimental...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 65. Data-Driven Antenna Delay Calibration for UWB Devices for Network Positioning

- Citation: Zuoya Liu, Teemu Hakala, Juha Hyyppä, Antero Kukko, Harri Kaartinen, Ruizhi Chen. Data-Driven Antenna Delay Calibration for UWB Devices for Network Positioning. IEEE Transactions on Instrumentation and Measurement, 2024. https://doi.org/10.1109/tim.2023.3348891
- Cluster(s): B
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: This study presents a real-time and fully automatic antenna delay calibration approach for ultrawideband (UWB) devices, which can be utilized to evaluate combined delay of each UWB device used in the positioning system. Two estimators, a coarse estimator and a fine-tuning estimator, operate closely together in the calibration. The coarse estimator can determine a common coarse value for all devices involved in the calibration; the fine-tuning estimator continuously determines the optimal value for each device. More than three UWB devices can be calibrated simultaneously in real time in the developed approach, making it a suitable solution for positioning applications with a large number of UWB devices. To evaluate the calibration accuracy of the proposed approach and verify the ranging accuracy and precision at different distances,...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 66. Measurement of Relative Position and Orientation using UWB

- Citation: Ernst-Johann Theussl, Dimitar Ninevski, Paul O'Leary. Measurement of Relative Position and Orientation using UWB. 2019 IEEE International Instrumentation and Measurement Technology Conference (I2MTC), 2019. https://doi.org/10.1109/i2mtc.2019.8827149
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: This paper introduces a new approach to measure relative positioning and orientation (RPO), by instrumenting mobile equipment with ultra-wideband (UWB) distance measurements. In this experiment RPO is tested without a surrounding stationary UWB anchor network; all necessary UWB devices are directly mounted on the machinery. This results in a simplified implementation in the industry, but also challenges the RPO determination. Due to this, the precision and uncertainty of the UWB measurements were characterized in a real application environment, i.e., on a quay to determine if a large body of water would influence the high frequency signals. It was determined that the UWB distance measurements had an uncertainty of approximately 20mm when measuring orthogonal to the antenna and 35mm at large angles; both results for 95% confidence....
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: mentions identifiability/observability/Fisher/CRB-style terms.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 67. Robust Parameter Estimation in Computer Vision

- Citation: Charles V. Stewart. Robust Parameter Estimation in Computer Vision. SIAM Review, 1999. https://doi.org/10.1137/s0036144598345802
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: robust estimation or outlier mitigation.
- Abstract/key finding read: Estimation techniques in computer vision applications must estimate accurate model parameters despite small-scale noise in the data, occasional large-scale measurement errors (outliers), and measurements from multiple populations in the same data set. Increasingly, robust estimation techniques, some borrowed from the statistics literature and others described in the computer vision literature, have been used in solving these parameter estimation problems. Ideally, these techniques should effectively ignore the outliers and measurements from other populations, treating them as outliers, when estimating the parameters of a single population. Two frequently used techniques are least-median of squares (LMS) [P. J. Rousseeuw, {J. Amer. Statist. Assoc., 79 (1984), pp. 871--880] and M-estimators [Robust Statistics: The Approach Based on...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 68. Benchmarking UWB-Based Infrastructure-Free Positioning and Multi-Robot Relative Localization: Dataset and Characterization

- Citation: Paola Torrico Morón, Sahar Salimpour, Lei Fu, Xianjia Yu, Jorge Peña Queralta, Tomi Westerlund. Benchmarking UWB-Based Infrastructure-Free Positioning and Multi-Robot Relative Localization: Dataset and Characterization. 2023 IEEE Sensors Applications Symposium (SAS), 2023. https://doi.org/10.1109/sas58821.2023.10254018
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Ultra-wideband (UWB) positioning has emerged as a low-cost and dependable localization solution for multiple use cases, from mobile robots to asset tracking within the Industrial IoT. The technology is mature and the scientific literature contains multiple datasets and methods for localization based on fixed UWB nodes. At the same time, research in UWB-based relative localization and infrastructure-free localization is gaining traction, further domains. tools and datasets in this domain are scarce. Therefore, we introduce in this paper a novel dataset for benchmarking infrastructure-free relative localization targeting the domain of multi-robot systems. Compared to previous datasets, we analyze the performance of different relative localization approaches for a much wider variety of scenarios with varying numbers of fixed and mobile...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 69. Improving the Accuracy of Decawave’s UWB MDEK1001 Location System by Gaining Access to Multiple Ranges

- Citation: Antonio R. Jiménez, Fernando Seco. Improving the Accuracy of Decawave’s UWB MDEK1001 Location System by Gaining Access to Multiple Ranges. Sensors, 2021. https://doi.org/10.3390/s21051787
- Cluster(s): A, D, F
- Read status: abstract_read_via_openalex
- Method: robust estimation or outlier mitigation.
- Abstract/key finding read: The location of people, robots, and Internet-of-Things (IoT) devices has become increasingly important. Among the available location technologies, solutions based on ultrawideband (UWB) radio are having much success due to their accuracy, which is ideally at a centimeter level. However, this accuracy is degraded in most common indoor environments due to the presence of obstacles which block or reflect the radio signals used for ranging. One way to circumvent this difficulty is through robust estimation algorithms based on measurement redundancy, permitting to minimize the effect of significantly erroneous ranges (outliers). This need for redundancy often conflicts with hardware restraints put up by the location system's designers. In this work, we present a procedure to increase the redundancy of UWB systems and demonstrate it with...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 70. Static and Dynamic Evaluation of an UWB Localization System for Industrial Applications

- Citation: Mickaël Delamare, Rémi Boutteau, Xavier Savatier, Nicolas Iriart. Static and Dynamic Evaluation of an UWB Localization System for Industrial Applications. Sci, 2020. https://doi.org/10.3390/sci2010007
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Many applications in the context of Industry 4.0 require precise localization. However, indoor localization remains an open problem, especially in complex environments such as industrial environments. In recent years, we have seen the emergence of Ultra WideBand (UWB) localization systems. The aim of this article is to evaluate the performance of a UWB system to estimate the position of a person moving in an indoor environment. To do so, we implemented an experimental protocol to evaluate the accuracy of the UWB system both statically and dynamically. The UWB system is compared to a ground truth obtained by a motion capture system with a millimetric accuracy.
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 71. Experimental Evaluation of a UWB-Based Cooperative Positioning System for Pedestrians in GNSS-Denied Environment

- Citation: Jelena Gabela, Guenther Retscher, Salil Goel, Harris Perakis, Andrea Masiero, Charles Toth, Vassilis Gikas, Allison Kealy, et al.. Experimental Evaluation of a UWB-Based Cooperative Positioning System for Pedestrians in GNSS-Denied Environment. Sensors, 2019. https://doi.org/10.3390/s19235274
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Cooperative positioning (CP) utilises information sharing among multiple nodes to enable positioning in Global Navigation Satellite System (GNSS)-denied environments. This paper reports the performance of a CP system for pedestrians using Ultra-Wide Band (UWB) technology inGNSS-denied environments. This data set was collected as part of a benchmarking measurementcampaign carried out at the Ohio State University in October 2017. Pedestrians were equippedwith a variety of sensors, including two different UWB systems, on a specially designed helmetserving as a mobile multi-sensor platform for CP. Different users were walking in stop-and-go modealong trajectories with predefined checkpoints and under various challenging environments. Inthe developed CP network, both Peer-to-Infrastructure (P2I) and Peer-to-Peer (P2P) measurementsare used...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 72. Multi-Agent Relative Pose Estimation with UWB and Constrained Communications

- Citation: Andrew Fishberg, Jonathan P. How. Multi-Agent Relative Pose Estimation with UWB and Constrained Communications. 2022 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS), 2022. https://doi.org/10.1109/iros47612.2022.9982005
- Cluster(s): B
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Inter-agent relative localization is critical for any multi-robot system operating in the absence of external positioning infrastructure or prior environmental knowledge. We propose a novel inter-agent relative 2D pose estimation system where each participating agent is equipped with several ultra-wideband (UWB) ranging tags. Prior work typically supplements noisy UWB range measurements with additional continuously transmitted data, such as odometry, making these approaches scale poorly with increased swarm size or decreased communication throughput. This approach addresses these concerns by using only locally collected UWB measurements with no additionally transmitted data. By modeling observed ranging biases and systematic antenna obstructions in our proposed optimization solution, our experimental results demonstrate an improved...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 73. Multipath-Assisted Single-Anchor Localization via Deep Variational Learning

- Citation: Tianyu Wang, Yuxiao Li, Junchen Liu, Keke Hu, Yuan Shen. Multipath-Assisted Single-Anchor Localization via Deep Variational Learning. IEEE Transactions on Wireless Communications, 2024. https://doi.org/10.1109/twc.2024.3359047
- Cluster(s): A, C
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Location awareness plays an increasingly important role in wireless network applications. However, accurate localization in complex indoor environments remains challenging for existing radio frequency (RF)-based systems, among which the ultra-wide bandwidth (UWB) technology ranks to be the most promising one due to its capability in providing channel information with fine time resolution. In this paper, we propose a multipath-assisted single-anchor localization framework that can provide high-accuracy positional information in complex indoor environments. Specifically, a deep variational learning method is proposed to produce calibrated estimates of position-related parameters, including distance, time-difference-of-arrival and angle-of-arrival, which are then fed into a multipath-assisted single-anchor localization algorithm. The...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 74. A Robust Extended Kalman Filter Applied to Ultrawideband Positioning

- Citation: Chuanyang Wang, Houzeng Han, Jian Wang, Hang Yu, Yang Deng. A Robust Extended Kalman Filter Applied to Ultrawideband Positioning. Mathematical Problems in Engineering, 2020. https://doi.org/10.1155/2020/1809262
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: robust estimation or outlier mitigation.
- Abstract/key finding read: Ultrawideband (UWB) is well-suited for indoor positioning due to its high resolution and good penetration through objects. The observation model of UWB positioning is nonlinear. As one of nonlinear filter algorithms, extended Kalman filter (EKF) is widely used to estimate the position. In practical applications, the dynamic estimation is subject to the outliers caused by gross errors. However, the EKF cannot resist the effect of gross errors. The innovation will become abnormally large and the performance and the reliability of the filter algorithm are inevitably influenced. In this study, a robust EKF (REKF) method accompanied by hypothesis test and robust estimation is proposed. To judge the validity of model, the global test based on Mahalanobis distance is implemented to assess whether the test statistical term exceeds the...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 75. Evaluation of Grid-Based Uncertainty Propagation for Collaborative Self-Calibration in Indoor Positioning Systems

- Citation: Paul Schwarzbach, Andrea Jung. Evaluation of Grid-Based Uncertainty Propagation for Collaborative Self-Calibration in Indoor Positioning Systems. IEEE Journal of Indoor and Seamless Positioning and Navigation, 2026. https://doi.org/10.1109/jispin.2026.3687458
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: robust estimation or outlier mitigation.
- Abstract/key finding read: Radio-based localization systems conventionally require stationary reference points (e.g. anchors) with precisely surveyed positions, making deployment time-consuming and costly. This paper presents an empirical evaluation of collaborative self-calibration for Ultra Wideband (UWB) networks, extending a Bayesian approach based on grid-based uncertainty propagation. The enhanced algorithm reduces measurement availability requirements while maintaining positioning accuracy through probabilistic state estimation. We validate the approach using real-world data from controlled indoor experiments with 12 nodes in a static environment. Experimental evaluation yields 0.28 m mean ranging error under line-of sight conditions and 1.11 m overall ranging error across mixed propagation scenarios. Results confirm the algorithm's resilience to...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 76. FWAF-VID: A Flapping-Wing Aggressive Flight Benchmark Dataset for Visual-Inertial Localization

- Citation: Ji Hai Jiang, Erzhen Pan, Wenfu Xu, Wei Sun, Jingyang Ye. FWAF-VID: A Flapping-Wing Aggressive Flight Benchmark Dataset for Visual-Inertial Localization. IEEE Robotics and Automation Letters, 2025. https://doi.org/10.1109/lra.2025.3560856
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Accurate state estimation of micro aerial vehicles (MAVs) in high-speed and dynamic environments poses a significant challenge for visual-inertial odometry (VIO) algorithms. Flapping-wing aerial vehicles (FWAVs), as an emerging flight platform, have attracted significant attention for stealth capabilities and efficient flight characteristics. Despite the availability of numerous MAV visual-inertial datasets for six-degree-of-freedom (6-DoF) state estimation, these datasets are not applicable for FWAVs with pronounced vibrations and agile maneuverability. To address this gap, we propose a large-scale flapping-wing aggressive flight visual-inertial dataset FWAF-VID. It contains diverse integrations of synchronized onboard cameras and IMU sensors. A total of 28 sequences include static flapping, calibration, and real-world flights...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 77. Self calibration of the anchor nodes for UWB-IR TDOA based indoor positioning system

- Citation: Ankush Vashistha, Ankur Kumar Gupta, Choi Look Law. Self calibration of the anchor nodes for UWB-IR TDOA based indoor positioning system. 2018 IEEE 4th World Forum on Internet of Things (WF-IoT), 2018. https://doi.org/10.1109/wf-iot.2018.8355163
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: The problem of anchor nodes placement in indoor positioning systems is labor intensive and time consuming process. A self-calibrating scheme is proposed to determine the position of the anchor nodes using Ultra-Wide band impulse radio (UWB-IR). These positions can be further used to determine the position of the target nodes. The time difference of arrival measurement technique is employed to self-calibrate the anchor nodes. The proposed scheme is verified with the simulation results, as well as with an in house designed sensor nodes experimental setup.
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 78. Robust UAV Relative Navigation With DGPS, INS, and Peer-to-Peer Radio Ranging

- Citation: Jason N. Gross, Yu Gu, Matthew B. Rhudy. Robust UAV Relative Navigation With DGPS, INS, and Peer-to-Peer Radio Ranging. IEEE Transactions on Automation Science and Engineering, 2015. https://doi.org/10.1109/tase.2014.2383357
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: This paper considers the fusion of carrier-phase differential GPS (CP-DGPS), peer-to-peer ranging radios, and low-cost inertial navigation systems (INS) for the application of relative navigation of small unmanned aerial vehicles (UAVs) in close formation-flight. A novel sensor fusion algorithm is presented that incorporates locally processed tightly coupled GPS/INS-based absolute navigation solutions from each UAV in a relative navigation filter that estimates the baseline separation using integer-fixed relative CP-DGPS and a set of peer-to-peer ranging radios. The robustness of the dynamic baseline estimation performance under conditions that are typically challenging for CP-DGPS alone, such as a high occurrence of phase breaks, poor satellite visibility/geometry due to extreme UAV attitude, and heightened multipath intensity,...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 79. UWB Localization System for Indoor Applications: Concept, Realization and Analysis

- Citation: Łukasz Zwirełło, Tom Schipper, Marlene Harter, Thomas Zwick. UWB Localization System for Indoor Applications: Concept, Realization and Analysis. Journal of Electrical and Computer Engineering, 2012. https://doi.org/10.1155/2012/849638
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: A complete impulse‐based ultrawideband localization demonstrator for indoor applications is presented. The positioning method, along with the method of positioning error predicting, based on scenario geometry, is described. The hardware setup, including UWB transceiver and time measurement module, as well as the working principles is explained. The system simulation, used as a benchmark for the quality assessment of the performed measurements, is presented. Finally, the measurement results are discussed. The precise analysis of potential error sources in the system is conducted, based on both simulations and measurement. Furthermore, the methods, how to improve the average accuracy of 9 cm by including the influences of antennas and signal‐detection threshold level, are made. The localization accuracy, resulting from those...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 80. Monte Carlo Localization in Dense Multipath Environments Using UWB Ranging

- Citation: Damien Jourdan, John Deyst, Moe Z. Win, Nicholas Roy. Monte Carlo Localization in Dense Multipath Environments Using UWB Ranging. 2005 IEEE International Conference on Ultra-Wideband, 2006. https://doi.org/10.1109/icu.2005.1570005
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: For most outdoor applications, systems such as GPS provide users with accurate position estimates. However, reliable range-based localization using radio signals in indoor or urban environments can be a problem due to multipath fading and line-of-sight (LOS) blockage. The measurement bias introduced by these delays causes significant localization error, even when using additional sensors such as an inertial measurement unit (IMU) to perform outlier rejection. We describe an algorithm for accurate indoor localization of a sensor in a network of known beacons. The sensor measures the range to the beacons using an Ultra-Wideband (UWB) signal and uses statistical inference to infer and correct for the bias due to LOS blockage in the range measurements. We show that a particle filter can be used to estimate the joint distribution over both...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 81. Non-Invasive Driver Drowsiness Detection System

- Citation: Hafeez Ur Rehman Siddiqui, Adil Ali Saleem, R. H. Brown, Bahattin Bademci, Ernesto Lee, Furqan Rustam, Sandra Dudley. Non-Invasive Driver Drowsiness Detection System. Sensors, 2021. https://doi.org/10.3390/s21144833
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: Drowsiness when in command of a vehicle leads to a decline in cognitive performance that affects driver behavior, potentially causing accidents. Drowsiness-related road accidents lead to severe trauma, economic consequences, impact on others, physical injury and/or even death. Real-time and accurate driver drowsiness detection and warnings systems are necessary schemes to reduce tiredness-related driving accident rates. The research presented here aims at the classification of drowsy and non-drowsy driver states based on respiration rate detection by non-invasive, non-touch, impulsive radio ultra-wideband (IR-UWB) radar. Chest movements of 40 subjects were acquired for 5 m using a lab-placed IR-UWB radar system, and respiration per minute was extracted from the resulting signals. A structured dataset was obtained comprising...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 82. NLOS Identification- and Correction-Focused Fusion of UWB and LiDAR-SLAM Based on Factor Graph Optimization for High-Precision Positioning with Reduced Drift

- Citation: Zhijian Chen, Aigong Xu, Xin Sui, Yuting Hao, Cong Zhang, Zhengxu Shi. NLOS Identification- and Correction-Focused Fusion of UWB and LiDAR-SLAM Based on Factor Graph Optimization for High-Precision Positioning with Reduced Drift. Remote Sensing, 2022. https://doi.org/10.3390/rs14174258
- Cluster(s): A, C, D, F
- Read status: abstract_read_via_openalex
- Method: factor graph / graph optimization.
- Abstract/key finding read: In this study, we propose a tightly coupled integrated method of ultrawideband (UWB) and light detection and ranging (LiDAR)-based simultaneous localization and mapping (SLAM) for global navigation satellite system (GNSS)-denied environments to achieve high-precision positioning with reduced drift. Specifically, we focus on non-line-of-sight (NLOS) identification and correction. In previous work, we utilized laser point cloud maps to identify and exclude NLOS measurements in real time to attenuate their severe effects on the integrated system. However, the complete exclusion of NLOS measurements will likely lead to deterioration in the dilution of precision (DOP) for the remaining line-of-sight (LOS) anchors, counterproductively introducing large positioning errors into the integrated system. Therefore, this study considers the...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: mentions identifiability/observability/Fisher/CRB-style terms.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 83. UWB-Based Localization System Considering Antenna Anisotropy and NLOS/Multipath Conditions

- Citation: Taekyun Kim, Byoungkwon Yoon, Dongjun Lee. UWB-Based Localization System Considering Antenna Anisotropy and NLOS/Multipath Conditions. 2024 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS), 2024. https://doi.org/10.1109/iros58592.2024.10802170
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Ultra-wideband (UWB) communication technology has gained attention in robotics due to its ability to provide range measurements possibly with centimeter-level accuracy. Nevertheless, practical UWB range measurements are susceptible to disturbances from multiple sources, including the anisotropic characteristics of antennas, non-line-of-sight (NLOS) conditions, and multipath propagation. In this paper, we introduce a UWB range measurement model that addresses these sources of error. To accommodate the effects of antenna anisotropy, we adopt real spherical harmonics to represent directional bias in the UWB range measurement model. To handle delayed measurements induced by NLOS conditions and multipath propagation, an asymmetric heavy-tailed distribution is utilized to model the measurement noise. We calibrate this measurement model...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 84. A Robust Detection and Optimization Approach for Delayed Measurements in UWB Particle-Filter-Based Indoor Positioning

- Citation: Ning Zhou, Lawrence Lau, Ruibin Bai, Terry Moore. A Robust Detection and Optimization Approach for Delayed Measurements in UWB Particle-Filter-Based Indoor Positioning. NAVIGATION Journal of the Institute of Navigation, 2022. https://doi.org/10.33012/navi.514
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: <h3>Abstract</h3> Ultrawideband (UWB) technology has received considerable attention in indoor positioning because of its high ranging accuracy. However, UWB range measurements can be contaminated by the delayed signals resulting from obstruction and reflection in difficult indoor environments. These signals introduce delays to range measurements and degrade positioning accuracy if they are not resolved properly. In order to mitigate the effects of delayed range measurements on positioning and achieve a high-accuracy position estimation, this paper proposes a robust particle-filter-based indoor positioning algorithm. In the proposed algorithm, an outlier detection method is proposed for delayed measurement identification, and a constrained particle sampling method is proposed to optimize the distribution of the predicted particles....
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 85. Fusion of GNSS Pseudoranges with UWB Ranges Based on Clustering and Weighted Least Squares

- Citation: Guenther Retscher, Dániel Kiss, Jelena Gabela. Fusion of GNSS Pseudoranges with UWB Ranges Based on Clustering and Weighted Least Squares. Sensors, 2023. https://doi.org/10.3390/s23063303
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: Global navigation satellite systems (GNSSs) and ultra-wideband (UWB) ranging are two central research topics in the field of positioning and navigation. In this study, a GNSS/UWB fusion method is investigated in GNSS-challenged environments or for the transition between outdoor and indoor environments. UWB augments the GNSS positioning solution in these environments. GNSS stop-and-go measurements were carried out simultaneously to UWB range observations within the network of grid points used for testing. The influence of UWB range measurements on the GNSS solution is examined with three weighted least squares (WLS) approaches. The first WLS variant relies solely on the UWB range measurements. The second approach includes a measurement model that utilizes GNSS only. The third model fuses both approaches into a single multi-sensor...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 86. Janus

- Citation: Timofei Istomin, Elia Leoni, Davide Molteni, Amy L. Murphy, Gian Pietro Picco, Maurizio Griva. Janus. Proceedings of the ACM on Interactive Mobile Wearable and Ubiquitous Technologies, 2021. https://doi.org/10.1145/3494978
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: Proximity detection is at the core of several mobile and ubiquitous computing applications. These include reactive use cases, e.g., alerting individuals of hazards or interaction opportunities, and others concerned only with logging proximity data, e.g., for offline analysis and modeling. Common approaches rely on Bluetooth Low Energy (BLE) or ultra-wideband (UWB) radios. Nevertheless, these strike opposite tradeoffs between the accuracy of distance estimates quantifying proximity and the energy efficiency affecting system lifetime, effectively forcing a choice between the two and ultimately constraining applicability. Janus reconciles these dimensions in a dual-radio protocol enabling accurate and energy-efficient proximity detection, where the energy-savvy BLE is exploited to discover devices and coordinate their distance...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 87. Measurement Analysis and Channel Modeling for TOA-Based Ranging in Tunnels

- Citation: Vladimir Savic, Javier Ferrer-Coll, Per Angskog, Jose Chilo, Peter Stenumgaard, Erik G. Larsson. Measurement Analysis and Channel Modeling for TOA-Based Ranging in Tunnels. IEEE Transactions on Wireless Communications, 2014. https://doi.org/10.1109/twc.2014.2350493
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: robust estimation or outlier mitigation.
- Abstract/key finding read: A robust and accurate positioning solution is required to increase the safety in GPS-denied environments. Although there is a lot of available research in this area, little has been done for confined environments such as tunnels. Therefore, we organized a measurement campaign in a basement tunnel of Linköping university, in which we obtained ultra-wideband (UWB) complex impulse responses for line-of-sight (LOS), and three non-LOS (NLOS) scenarios. This paper is focused on time-of-arrival (TOA) ranging since this technique can provide the most accurate range estimates, which are required for range-based positioning. We describe the measurement setup and procedure, select the threshold for TOA estimation, analyze the channel propagation parameters obtained from the power delay profile (PDP), and provide statistical model for ranging....
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 88. Validity of an ultra-wideband local positioning system to assess specific movements in handball

- Citation: Antoine Fleureau, Mathieu Lacome, Martin Buchheit, Antoine Couturier, Giuseppe Rabita. Validity of an ultra-wideband local positioning system to assess specific movements in handball. Biology of Sport, 2020. https://doi.org/10.5114/biolsport.2020.96850
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: The aim of this study was to examine the concurrent validity of the Kinexon local positioning system (LPS) in comparison with the Vicon motion capture system used as the reference. Five recreationally active men performed ten repetitions of linear sprints, medio-lateral side-to-side and handball-specific movements both in the centre and on the side of an indoor field. Validity was assessed for peak speed, peak acceleration and peak deceleration using standardised biases, Pearson coefficient of correlation (r), and standardised typical error of the estimate. With the exception of peak decelerations during specific movements in the centre and peak acceleration and deceleration during linear sprints on the side of the field, the standardised typical error of the estimate (TEE) values were all small to moderate (0.06-0.48), standardised...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 89. Necessary and Sufficient Conditions for Observability of SLAM-Based TDOA Sensor Array Calibration and Source Localization

- Citation: Daobilige Su, He Kong, Salah Sukkarieh, Shoudong Huang. Necessary and Sufficient Conditions for Observability of SLAM-Based TDOA Sensor Array Calibration and Source Localization. IEEE Transactions on Robotics, 2021. https://doi.org/10.1109/tro.2021.3069140
- Cluster(s): E
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Sensor array-based systems, which adopt time difference of arrival (TDOA) measurements among the sensors, have found many robotic applications. However, for existing frameworks and systems to be useful, the sensor array needs to be calibrated accurately. Of particular interest in this article are microphone array-based robot audition systems. In our recent work, by using a moving sound source, and the graph-based formulation of simultaneous localization and mapping (SLAM), we have proposed a framework for joint sound source localization and calibration of microphone array geometrical information, together with the estimation of microphone time offset and clock difference/drift rates. However, a thorough study on the identifiability question, termed observability analysis here, in the SLAM framework for microphone array calibration and...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: mentions identifiability/observability/Fisher/CRB-style terms.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 90. Robust indoor positioning fusing PDR and RF technologies: The RFID and UWB case

- Citation: Francisco Zampella, Antonio R. Jiménez, Fernando Seco. Robust indoor positioning fusing PDR and RF technologies: The RFID and UWB case. International Conference on Indoor Positioning and Indoor Navigation, 2013. https://doi.org/10.1109/ipin.2013.6817857
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Indoor positioning is usually based on individual technologies that provide estimates of the trajectory of the person, or measures the ranges or angles between the user and known positions. Each technique has its advantages and problems, and a common way to overcome the drawbacks of single-technology solutions is to fuse the information from several system, but due to their non linear measurements, there is no optimal linear solution. We propose the use of a particle filter to fuse foot mounted inertial measurements with any additional Radio Frequency (RF) measurement. The information fusion is achieved propagating the position of the particles using the relative step displacements obtained from foot mounted Pedestrian Dead Reckoning (PDR), and updating the weights of the particles according to the RF measurements. In our experiments...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 91. Ranging With Ultrawide Bandwidth Signals in Multipath Environments

- Citation: Davide Dardari, Andrea Conti, Ulric J. Ferner, Andrea Giorgetti, Moe Z. Win. Ranging With Ultrawide Bandwidth Signals in Multipath Environments. Proceedings of the IEEE, 2009. https://doi.org/10.1109/jproc.2008.2008846
- Cluster(s): E
- Read status: abstract_read_via_openalex
- Method: method not resolved from abstract metadata.
- Abstract/key finding read: <para xmlns:mml="http://www.w3.org/1998/Math/MathML" xmlns:xlink="http://www.w3.org/1999/xlink"> Over the coming decades, high-definition situationally-aware networks have the potential to create revolutionary applications in the social, scientific, commercial, and military sectors. Ultrawide bandwidth (UWB) technology is a viable candidate for enabling accurate localization capabilities through time-of-arrival (TOA)-based ranging techniques. These techniques exploit the fine delay resolution property of UWB signals by estimating the TOA of the first signal path. Exploiting the full capabilities of UWB TOA estimation can be challenging, especially when operating in harsh propagation environments, since the direct path may not exist or it may not be the strongest. In this paper, we first give an overview of ranging techniques together...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: mentions identifiability/observability/Fisher/CRB-style terms.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 92. Two-stage acquisition for UWB in dense multipath

- Citation: Jihad Ibrahim, R. Michael Buehrer. Two-stage acquisition for UWB in dense multipath. IEEE Journal on Selected Areas in Communications, 2006. https://doi.org/10.1109/jsac.2005.863832
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: robust estimation or outlier mitigation.
- Abstract/key finding read: Traditional synchronization techniques applied to impulse-radio ultra-wideband (UWB) result in prohibitively long acquisition times, due to the extremely large search space. Additionally, in dense multipath environments, there exist a larger number of cells within the uncertainty region that can lead to acquisition lock. Locking to an arbitrary multipath component may result in unacceptable performance for many applications (range error in positioning systems for example). In this paper, we present a modified framework for the analysis of UWB acquisition which accommodates multiple lock cells. The framework divides the acquisition process into two distinct phases. The two phases are termed "coarse" and "fine" acquisition. The coarse acquisition phase is a fast implementation of traditional serial search which takes advantage of the...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 93. MIDAS robust trend estimator for accurate GPS station velocities without step detection

- Citation: Geoffrey Blewitt, Corné Kreemer, W. C. Hammond, Julien Gazeaux. MIDAS robust trend estimator for accurate GPS station velocities without step detection. Journal of Geophysical Research Solid Earth, 2016. https://doi.org/10.1002/2015jb012552
- Cluster(s): F
- Read status: abstract_read_via_openalex
- Method: least-squares or nonlinear optimization.
- Abstract/key finding read: Abstract Automatic estimation of velocities from GPS coordinate time series is becoming required to cope with the exponentially increasing flood of available data, but problems detectable to the human eye are often overlooked. This motivates us to find an automatic and accurate estimator of trend that is resistant to common problems such as step discontinuities, outliers, seasonality, skewness, and heteroscedasticity. Developed here, Median Interannual Difference Adjusted for Skewness (MIDAS) is a variant of the Theil‐Sen median trend estimator, for which the ordinary version is the median of slopes v ij = ( x j –x i )/( t j –t i ) computed between all data pairs i &gt; j . For normally distributed data, Theil‐Sen and least squares trend estimates are statistically identical, but unlike least squares, Theil‐Sen is resistant to...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 94. UTIL: An ultra-wideband time-difference-of-arrival indoor localization dataset

- Citation: Wenda Zhao, Abhishek Goudar, Xinyuan Qiao, Angela P. Schoellig. UTIL: An ultra-wideband time-difference-of-arrival indoor localization dataset. The International Journal of Robotics Research, 2024. https://doi.org/10.1177/02783649241230640
- Cluster(s): D
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Ultra-wideband (UWB) time-difference-of-arrival (TDOA)-based localization has emerged as a promising, low-cost, and scalable indoor localization solution, which is especially suited for multi-robot applications. However, there is a lack of public datasets to study and benchmark UWB TDOA positioning technology in cluttered indoor environments. We fill in this gap by presenting a comprehensive dataset using Decawave’s DWM1000 UWB modules. To characterize the UWB TDOA measurement performance under various line-of-sight (LOS) and non-line-of-sight (NLOS) conditions, we collected signal-to-noise ratio (SNR), power difference values, and raw UWB TDOA measurements during the identification experiments. We also conducted a cumulative total of around 150 min of real-world flight experiments on a customized quadrotor platform to benchmark the...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 95. Node Calibration in UWB-Based RTLSs Using Multiple Simultaneous Ranging

- Citation: Shashi Shah, La-or Kovavisaruch, Kamol Kaemarungsi, Tanee Demeechai. Node Calibration in UWB-Based RTLSs Using Multiple Simultaneous Ranging. Sensors, 2022. https://doi.org/10.3390/s22030864
- Cluster(s): A, B
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: Ultra-wideband (UWB) networks are gaining wide acceptance in short- to medium-range wireless sensing and positioning applications in indoor environments due to their capability of providing high-ranging accuracy. However, the performance is highly related to the accuracy of measured position and antenna delay of anchor nodes, which form a reference positioning system of fixed infrastructure nodes. Usually, the position and antenna delay of the anchor nodes are measured separately as a standard initial procedure. Such separate measurement procedures require relatively more time and manual interventions. This paper presents a system that simultaneously measures the position and antenna delay of the anchor nodes. It provides comprehensive mathematical modeling, design, and implementation of the proposed system. An experimental evaluation...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 96. Antenna Delay-Independent Simultaneous Ranging for UWB-Based RTLSs

- Citation: Shashi Shah, Sushank Chaudhary, Rizwan Ullah, Amir Parnianifard, Muhammad Zain Siddiqi, Pisit Vanichchanunt, Wiroonsak Santipach, Lunchakorn Wuttisittikulkij. Antenna Delay-Independent Simultaneous Ranging for UWB-Based RTLSs. Journal of Sensor and Actuator Networks, 2022. https://doi.org/10.3390/jsan12010001
- Cluster(s): A, B
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: The ultra-wideband (UWB)-based real-time localization system (RTLS) is a promising technology for locating and tracking assets and personnel in real-time within a defined indoor environment since it provides high-ranging accuracy. However, its performance can be affected by the underlying antenna delays of UWB nodes, which act as a source of error during range estimations. Usually, measurement of the antenna delays is performed separately as a dedicated standalone procedure. Such an additional measurement procedure makes the UWB-based RTLS more tedious with manual interventions. Moreover, the air-time occupancy during the transmission and reception of signaling messages for range estimations between UWB node pairs also limits the serviceable capability of these networks. In this regard, we present a novel simultaneous ranging scheme...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 97. Two-Stage UWB Anchors’ Self-Calibration and Trajectory Localization

- Citation: Zijun Yang, Xia You, Yan Jiang, Hao Wang, Xiaolong Wu, Qingxi Zeng. Two-Stage UWB Anchors’ Self-Calibration and Trajectory Localization. IEEE Sensors Journal, 2025. https://doi.org/10.1109/jsen.2025.3630330
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: SLAM or inertial-aided calibration/localization.
- Abstract/key finding read: Ultra Wide Band (UWB) localization system relies on fixed anchors and mobile tags for localization, where the coordinates of anchors critically influence the accuracy of tag position resolution. Existing positioning systems either assume predefined anchor positions or require high-precision sensors for anchor calibration, posing significant challenges in practical deployment. This paper proposes a novel two-stage progressive anchor self-calibration and trajectory localization method. In Stage I, the initial positions of anchors are determined through multidimensional scaling (MDS). Stage II achieves cost-effective high-precision anchor positioning and trajectory optimization through tight coupling of UWB, Inertial Measurement Unit (IMU), and Odometry, utilizing a hybrid approach combining distance-constrained iterative least squares...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: possibly addressed.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 98. Antenna Delay Calibration of UWB Nodes

- Citation: Shashi Shah, Krit Chaiwong, La-or Kovavisaruch, Kamol Kaemarungsi, Tanee Demeechai. Antenna Delay Calibration of UWB Nodes. IEEE Access, 2021. https://doi.org/10.1109/access.2021.3075448
- Cluster(s): A, B
- Read status: abstract_read_via_openalex
- Method: delay, offset, or ranging-bias calibration.
- Abstract/key finding read: Impulse-radio ultra-wideband (IR-UWB) networks are gaining wide acceptance in short-to-medium range wireless sensing and positioning applications that require high accuracy. It is achieved generally via signal message exchange between ultra-wideband (UWB) transceiver nodes, where the signal propagating through their analog circuitry suffers transmitting and receiving antenna delays. Such delays, unless measured and properly corrected for, may induce an error in range estimation between UWB nodes and eventually affect the accuracy of real-time location systems (RTLSs) based on the IR-UWB. This paper presents a system to measure the antenna delays of UWB nodes. It provides comprehensive mathematical modeling, design, and implementation of the proposed antenna delay measurement system. Experimental evaluation in a line-of-sight (LOS)...
- Joint delay with position: possibly addressed.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 99. Fast Self-calibration Method for Massive UWB Anchors Aided by Odometry

- Citation: Yiding Zhan, Zongqi Yu, Xiaowei Cui, Gang Liu, Mingquan Lu. Fast Self-calibration Method for Massive UWB Anchors Aided by Odometry. Proceedings of the Institute of Navigation ... International Technical Meeting/Proceedings of the ... International Technical Meeting of The Institute of Navigation, 2024. https://doi.org/10.33012/2024.19574
- Cluster(s): A
- Read status: abstract_read_via_openalex
- Method: factor graph / graph optimization.
- Abstract/key finding read: UWB(Ultra-Wide Band) is a promising technology to achieve precise positioning in GNSS denied indoor and outdoor areas. Centimeter-level positioning accuracy can be achieved by UWB when the precise positions of the anchors are determined. Fast and precise self-calibration of UWB Anchor in NLOS environment has been the bottleneck of large-scale UWB application. We propose a novel method to calibrate UWB anchors using a mobile UWB tag with odometry. Large-scale UWB systems in a variety of challenging positioning environments can be rapidly deployed and calibrated by this method. We establish a graph optimization model to solve this problem. Tag odometry and anchor coordinates are jointly optimized in the graph model. We collected multiple datasets using various sensors in different environments to validate the algorithm’s feasibility. In...
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.

## 100. UWB anchor nodes self-calibration in NLOS conditions: a machine learning and adaptive PHY error correction approach

- Citation: Matteo Ridolfi, Jaron Fontaine, Ben Van Herbruggen, Wout Joseph, Jeroen Hoebeke, Eli De Poorter. UWB anchor nodes self-calibration in NLOS conditions: a machine learning and adaptive PHY error correction approach. Wireless Networks, 2021. https://doi.org/10.1007/s11276-021-02631-0
- Cluster(s): A, C
- Read status: metadata_read_via_openalex
- Method: machine-learning based classification or correction.
- Abstract/key finding read: No abstract exposed by OpenAlex; title, venue, DOI/URL, and cluster metadata were used.
- Joint delay with position: not addressed in paper abstract/metadata.
- Identifiability/coupling analysis: not addressed in paper abstract/metadata.
- External ground truth: not addressed in paper abstract/metadata.
- Critical: wrong metric calibration wins? not addressed in paper abstract/metadata.
- Critical: scale-delay/common-mode coupling? not addressed in paper abstract/metadata.
