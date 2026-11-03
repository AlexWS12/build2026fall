"""What the renderer's generated test clips contain, shared by the fixtures and the tests."""

WIDTH, HEIGHT, FPS, SECONDS = 320, 240, 30, 3
FRAMES = FPS * SECONDS
TONE_HZ, SAMPLE_RATE = 440, 48000
TONE_RMS = 1 / (8 * 2**0.5)  # ffmpeg's sine source has amplitude 1/8
