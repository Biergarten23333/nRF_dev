def timer32_delta(current,previous):return ((int(current)-int(previous))&0xffffffff)
def native_dt_seconds(current_us,previous_us):
 d=int(current_us)-int(previous_us)
 if d<=0:raise ValueError("non-increasing widened TIMER2")
 return d*1e-6
