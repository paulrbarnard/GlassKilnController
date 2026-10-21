"""Sharpness score for one camera frame, used by scripts/focus.sh.

Runs inside the app container (it has OpenCV; the board's own Python
doesn't). Finds the lit 7-segment digits as the bright region of the frame
and scores only that, so the dark background doesn't swamp the number.
Prints one line: score, digit-region size, and its share of frame width.
"""

import sys

import cv2
import numpy as np

frame = cv2.imread(sys.argv[1])
if frame is None:
    print("no frame")
    sys.exit(1)
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

# The digits are by far the brightest thing in view: take everything above
# half the peak brightness, then pad its bounding box so the edges count.
_, mask = cv2.threshold(gray, int(gray.max()) // 2, 255, cv2.THRESH_BINARY)
points = cv2.findNonZero(mask)
if points is None or gray.max() < 40:
    print("no digits found (display off or out of view?)")
    sys.exit(0)
x, y, w, h = cv2.boundingRect(points)
pad = max(w, h) // 10
x0, y0 = max(x - pad, 0), max(y - pad, 0)
roi = gray[y0:y + h + pad, x0:x + w + pad].astype(np.float64)

# Variance of the Laplacian (edge strength), divided by brightness squared
# so the auto-exposure drifting between frames doesn't move the score.
lap = cv2.Laplacian(roi, cv2.CV_64F)
score = lap.var() / max(roi.mean(), 1.0) ** 2 * 100
print(f"{score:7.1f}  digits {w}x{h}px  ({100 * w / gray.shape[1]:.0f}% of width)")
