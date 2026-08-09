# V45 STATUS baseline — BSF6C53 on b306-v45r7-val, healthy, 2026-08-09

Captured with the board running normally, no injection active, after the
post-injection reboot. Keep for differencing.

    STATUS fw=b306-imu-relay-v45 id=6C53 up_ms=331248 frames=2747
           strobe_rise=2760 imu=1/200Hz/N10 verify=PASS

    V45 present=0 seq=0 cause=0 len=1020 pages=5 core=1020 ch=4 ring=510
        flash=0 armed=1 blind_ms=0 blind_ticks=0 blind_discards=0
        dog=0 dog_dwell=0 dog_age_ms=0 dog_tick_ms=0

    RING boot=2 init=retained count=510/510 pages=102 frozen=1 reason=1
         fidx=510 fms=38743 writes=740 period=50 span=25500 view=0/0 ttl=30000

    CORPSE present=0 seq=0 pages=0 len=840 stage=0 stage_seq=0 age_ms=0
           trigger=0 rr=00000000 reboot_owner=3

    STALL e=3896 x=3895 age=0 s=0 td=0/0/0 q=0/0/0 hb=2701 rc=0
          rcc=0/0/0/0 alarm=0/0 test=00000000

NOTE: this is NOT a clean baseline. `frozen=1` and `reboot_owner=3` are
residue of the injection under investigation. A truly clean baseline needs a
power cycle first. Recorded as-is because it is the state the diagnosis below
was made from.
