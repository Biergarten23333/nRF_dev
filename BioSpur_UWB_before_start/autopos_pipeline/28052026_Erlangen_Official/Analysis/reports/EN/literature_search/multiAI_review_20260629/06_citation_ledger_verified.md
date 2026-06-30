# CITATION LEDGER — WEB + PDF-VERIFIED 2026-06-29 (resolves arbitration item A4)
# All 7 downloadable papers checked against the ACTUAL PDF first page (~/Downloads/papers/).
# "FIX" = confirmed real error in our bib.
#
# >>> REVERSAL the PDF check caught (all 5 AIs + my web-only ledger had it BACKWARDS):
#     "Chaiwong" belongs to SHAH 2021 (Antenna Delay Calibration), NOT Shah 2022 (Node Calibration).
#     The real Shah-2022 (s22030864) authors = Shah, Kovavisaruch, Kaemarungsi, Demeechai (NO Chaiwong).
#     => Do NOT "add Chaiwong to B's 2022" (that would INTRODUCE an error). B's 2022 is already correct.
#     => A-long has BOTH Shah entries wrong: 2021 missing Chaiwong (+invented Charaspreedalarp),
#        2022 wrongly INCLUDES Chaiwong. B-long has both correct.

## CONFIRMED HARD ERRORS (must-fix)

1. shah_2019  (A-long L468)  — WRONG AUTHORS (misattribution)
   We wrote : "Shah, C.L., Shin, S.-Y. and Jeon, J., 2019. Numerical and experimental evaluation
              of error estimation for two-way ranging methods. Sensors, 19(3), 616."
   TRUTH    : authors = Cung Lian Sang, Michael Adams, Timm Hörmann, Marc Hesse, Mario Porrmann,
              Ulrich Rückert (Bielefeld Univ). Title + venue (Sensors 19(3) 616) are correct.
   DOI      : 10.3390/s19030616
   FIX      : replace the entire author list; rename key shah_2019 -> sang_2019 (key is misleading).

2. shah_2021  (A-long L471-473) — WRONG AUTHORS + WRONG PAGES (two papers conflated)
   We wrote : "Shah, S., Kaemarungsi, K., Demeechai, T. and Charaspreedalarp, C., 2021. Antenna
              delay calibration of UWB nodes. IEEE Access, 9, pp.52030-52044."
   TRUTH    : authors = Shashi Shah, Krit Chaiwong, La-or Kovavisaruch, Kamol Kaemarungsi,
              Tanee Demeechai. IEEE Access, vol 9, pp. 63294-63305.
   DOI      : 10.1109/ACCESS.2021.3075448
   NOTE     : B-long's shah_2021 author list is ALREADY CORRECT -> align A to B, and fix pages.
   FIX      : replace author list (drop the invented "Charaspreedalarp, C."), fix pages -> 63294-63305.

## CONFIRMED CITATION-KEY COLLISIONS (same "Author YYYY", two different papers -> disambiguate a/b)
   Because A and B circulate as a PAIR (user gate G2), a reviewer reads both bibs together.

3. Piavanini 2022 — TWO DIFFERENT PAPERS, both real, same author group:
   2022a (A-long L459) : "A self-calibrating localization solution for sport applications with UWB
                         technology." Sensors, 22(23), 9363.   DOI 10.3390/s22239363
   2022b (B-long L491) : "A calibration method for antenna delay estimation and anchor self-
                         localization in UWB systems." Proc. 2022 IEEE MetroInd4.0&IoT, Trento,
                         7-9 Jun 2022, pp. 173-177.   DOI 10.1109/MetroInd4.0IoT54413.2022.9831579
                         (Crossref-confirmed; IEEE doc 9831579)
   FIX      : relabel as Piavanini 2022a / 2022b in BOTH documents.

4. Ridolfi 2021 — TWO DIFFERENT PAPERS, both real (confirmed in-repo, no web needed):
   2021a (A-long L463) : "UWB anchor nodes self-calibration in NLOS conditions: a machine learning
                         and adaptive PHY error correction approach." Wireless Networks, 27, 3007-3023.
   2021b (B-long L494) : "Self-calibration and collaborative localization for UWB positioning
                         systems." ACM Computing Surveys.
   FIX      : relabel as Ridolfi 2021a / 2021b in BOTH documents.

## CONFIRMED MINOR (accuracy / completeness)

5. yuan_2024  (A-long L488) — INCOMPLETE AUTHOR LIST
   We wrote : "Yuan, S., Nguyen, T.-M., Cao, M., Xu, X., Li, J. and Xie, L." (6)
   TRUTH    : ~10 authors; we omit Boyang Lou, Pengyu Yin, Jie Xu, Siyu Chen.
   ID       : arXiv:2412.16880 (also accepted to IEEE ICRA 2025).
   FIX      : complete the author list (et al. is fine if style allows; if listing, add the 4).

6. shah_2022 (Node Calibration in UWB-Based RTLSs; Sensors 2022, 22, 864; DOI 10.3390/s22030864)
   PDF-VERIFIED authors = Shashi Shah, La-or Kovavisaruch, Kamol Kaemarungsi, Tanee Demeechai (NO Chaiwong).
   A-long L475 WRONGLY INCLUDES "Chaiwong, K." -> REMOVE it (this is an A error, opposite of what reviewers said).
   B-long L496 + B-short are ALREADY CORRECT (no Chaiwong) -> LEAVE AS-IS.
   (All 5 reviewers said "add Chaiwong to B" — that is WRONG and would introduce an error. Chaiwong is on
    Shah 2021, not Shah 2022.)

7. batstone_2017 (B-long L466) — process TODO left in bib: "[verify exact venue/pages against PDF
   before submission]". A-long L429 is clean.
   TRUTH    : IPIN 2017; DOI 10.1109/IPIN.2017.8115885 (confirm pages on Xplore).
   FIX      : remove the bracketed note, insert real venue/DOI.

## CONFIRMED MISSING COMPETITOR (not an error — completeness, optional)

8. Liu & Cao 2025 — "Robust simultaneous UWB-anchor calibration and robot localization for
   emergency situations." Authors Xinghua Liu, Ming Cao. Factor-graph, UWB+LiDAR, 4 anchors
   auto-calibrated <30 s.   arXiv:2503.22272 (code: github.com/LiuxhRobotAI/Simultaneous_calibration_localization)
   ADD      : to A-long Table 1, "3D but external-sensor / LiDAR-dependent" cluster (does NOT break
              the inter-anchor-only claim).

## DOWNLOAD LIST (DOIs for the user)
  10.3390/s19030616            (Sang 2019 — was mis-cited as Shah 2019)
  10.1109/ACCESS.2021.3075448  (Shah 2021 — fix authors + pages)
  10.3390/s22239363            (Piavanini 2022a, sport)
  10.1109/MetroInd4.0IoT54413.2022.9831579  (Piavanini 2022b, MetroInd4.0&IoT, pp.173-177)
  arXiv:2412.16880             (Yuan 2024 — complete authors)
  arXiv:2503.22272             (Liu & Cao 2025 — add to Table 1)
  10.3390/s22030864            (Shah 2022 — REMOVE Chaiwong from A; B already correct, do NOT touch)
  10.1109/IPIN.2017.8115885    (Batstone 2017 — replace the TODO in B)
