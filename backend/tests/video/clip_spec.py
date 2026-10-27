"""What the generated test clips contain, shared by the fixtures and the tests."""

WIDTH, HEIGHT, FPS, SECONDS = 320, 240, 30, 2
FRAMES = FPS * SECONDS
GOP = 10  # a keyframe every 10 frames, so most seeks land between keyframes
# Frame i has luma LUMA_BASE + LUMA_STEP * i, inside video range (16-235) so nothing clips.
LUMA_BASE, LUMA_STEP = 16, 3
TONE_HZ, SAMPLE_RATE = 440, 48000
VFR_DROP = 5  # the VFR clip drops every frame whose index ≡ 2 (mod 5)
AUDIO_DELAY = 0.5  # seconds the delayed-audio clip's sound starts after its first frame
