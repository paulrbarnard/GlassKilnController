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
# Score at a fixed scale so the number doesn't depend on capture size.
scale = 1000 / gray.shape[1]
gray = cv2.resize(gray, (1000, round(gray.shape[0] * scale)))

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

# Stretch the region to its own contrast range, so exposure changes don't
# move the score, and blur lightly so sensor noise doesn't count as detail.
# The score is the strength of the sharpest edges (99th percentile of the
# gradient). A first version used Laplacian variance over mean brightness;
# that ranked a dim, blurred frame above a bright, sharp one.
lo, hi = np.percentile(roi, 1), np.percentile(roi, 99.5)
norm = cv2.GaussianBlur(np.clip((roi - lo) / max(hi - lo, 1.0), 0, 1), (3, 3), 0)
grad = np.hypot(cv2.Sobel(norm, cv2.CV_64F, 1, 0), cv2.Sobel(norm, cv2.CV_64F, 0, 1))
score = np.percentile(grad, 99) * 100
print(f"{score:7.1f}  digits {round(w / scale)}x{round(h / scale)}px  ({w / 10:.0f}% of width)")
