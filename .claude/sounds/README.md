# sounds/

`notify.wav` — the chime `scripts/notify.py` plays on the `Notification` and `Stop` hook
events. It is **generated, not downloaded**: 0.5 s of 16-bit mono PCM at 22.05 kHz (22 KB),
an 880 Hz sine plus a quieter 1320 Hz partial, exponential decay (τ = 0.14 s) with a 5 ms
fade-in so the onset does not click. Written with the stdlib's `wave` + `math` — no
dependency, no third-party audio, nothing to license. Regenerate it with:

```python
import math, struct, wave

rate, seconds, tau, attack, peak = 22050, 0.5, 0.14, 0.005, 0.55
frames = bytearray()
for i in range(int(rate * seconds)):
    t = i / rate
    tone = math.sin(2 * math.pi * 880.0 * t) + 0.45 * math.sin(2 * math.pi * 1320.0 * t)
    value = peak * math.exp(-t / tau) * min(1.0, t / attack) * tone / 1.45
    frames += struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767))
with wave.open("notify.wav", "wb") as out:
    out.setnchannels(1), out.setsampwidth(2), out.setframerate(rate)
    out.writeframes(bytes(frames))
```
