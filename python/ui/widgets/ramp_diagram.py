"""Ramp/hold/cool diagram widget — the visual heart of the "intuitive"
requirement: a plain-language picture of what a profile will actually do to
the glass, instead of the GM LT's own opaque menu-of-numbers UI.

Physically-derived (not decorative): x-axis is estimated real time, computed
from each segment's rate/target/hold exactly as the controller would run it
(see ``compute_points``). A segment run "as fast as possible" (rate 9999,
p.32) renders as a near-vertical jump, which is an accurate picture of what
that segment does, not a rendering bug.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from kiln.protocol import ANNEALING_END_F, PRE_ANNEALING_TARGET_F, RampHoldSegment

ROOM_TEMP_F = 75.0

BG_COLOR = QColor("#1b1b1f")
AXIS_COLOR = QColor("#55555c")
LINE_COLOR = QColor("#ff7a3d")
CURRENT_SEGMENT_COLOR = QColor("#ffd23d")
LABEL_COLOR = QColor("#e8e8ea")
ANNEAL_BAND_COLOR = QColor(80, 140, 255, 40)


@dataclass(frozen=True)
class PlotPoint:
    hours: float
    temp_f: float
    segment_index: int  # index into the original segments list


def compute_points(
    segments: list[RampHoldSegment], start_temp_f: float = ROOM_TEMP_F
) -> list[PlotPoint]:
    """Expand segments into (time, temperature) points: a ramp leg from the
    previous temperature to the segment's target at its rate, followed by a
    flat hold leg if hold_minutes > 0. Purely geometric — no controller
    interaction.
    """

    points = [PlotPoint(0.0, start_temp_f, -1)]
    t = 0.0
    temp = start_temp_f
    for i, seg in enumerate(segments):
        rate = max(seg.rate_f_per_hr, 1.0)
        delta = seg.target_temp_f - temp
        duration_h = abs(delta) / rate
        t += duration_h
        temp = seg.target_temp_f
        points.append(PlotPoint(t, temp, i))
        if seg.hold_minutes:
            t += seg.hold_minutes / 60.0
            points.append(PlotPoint(t, temp, i))
    return points


class RampDiagram(QWidget):
    """Renders a ramp/hold/cool profile as a temperature-vs-time line chart.

    Call ``set_segments()`` with the profile's resolved segments; call
    ``set_current_segment()`` while a firing is in progress to highlight
    where the kiln actually is (per ``sequencer.view_segment()``), so the
    diagram doubles as a live run-status display on ``RunScreen``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[RampHoldSegment] = []
        self._points: list[PlotPoint] = []
        self._current_segment: int | None = None
        self.setMinimumHeight(220)

    def set_segments(self, segments: list[RampHoldSegment]) -> None:
        self._segments = segments
        self._points = compute_points(segments)
        self.update()

    def set_current_segment(self, index: int | None) -> None:
        self._current_segment = index
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), BG_COLOR)

        if not self._points:
            painter.setPen(LABEL_COLOR)
            painter.drawText(self.rect(), Qt.AlignCenter, "No profile selected")
            painter.end()
            return

        margin_left, margin_right = 56, 24
        margin_top, margin_bottom = 20, 56
        plot_rect = QRectF(
            margin_left,
            margin_top,
            max(1, self.width() - margin_left - margin_right),
            max(1, self.height() - margin_top - margin_bottom),
        )

        max_hours = max(p.hours for p in self._points) or 1.0
        max_temp = max(p.temp_f for p in self._points) or 1.0
        min_temp = min(0.0, min(p.temp_f for p in self._points))

        def to_xy(p: PlotPoint) -> tuple[float, float]:
            x = plot_rect.left() + (p.hours / max_hours) * plot_rect.width()
            y = plot_rect.bottom() - (
                (p.temp_f - min_temp) / (max_temp - min_temp or 1.0)
            ) * plot_rect.height()
            return x, y

        self._draw_axes(painter, plot_rect, max_hours, max_temp, min_temp)
        self._draw_annealing_band(painter, plot_rect, max_temp, min_temp)
        self._draw_line(painter, to_xy)

        painter.end()

    def _draw_axes(
        self, painter: QPainter, plot_rect: QRectF, max_hours: float, max_temp: float, min_temp: float
    ) -> None:
        pen = QPen(AXIS_COLOR)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(plot_rect.bottomLeft(), plot_rect.bottomRight())
        painter.drawLine(plot_rect.topLeft(), plot_rect.bottomLeft())

        painter.setPen(LABEL_COLOR)
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, plot_rect.top() - 4, margin_or(plot_rect.left()), 16),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{max_temp:.0f}°F",
        )
        painter.drawText(
            QRectF(0, plot_rect.bottom() - 8, margin_or(plot_rect.left()), 16),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{min_temp:.0f}°F",
        )
        painter.drawText(
            QRectF(plot_rect.left(), plot_rect.bottom() + 6, plot_rect.width(), 16),
            Qt.AlignLeft,
            "0h",
        )
        painter.drawText(
            QRectF(plot_rect.left(), plot_rect.bottom() + 6, plot_rect.width(), 16),
            Qt.AlignRight,
            f"{max_hours:.1f}h total",
        )

    def _draw_annealing_band(
        self, painter: QPainter, plot_rect: QRectF, max_temp: float, min_temp: float
    ) -> None:
        # Shade the Annealing range (960 -> 800 F, p.24) for context, when
        # it's within the plotted temperature range.
        span = (max_temp - min_temp) or 1.0
        if min_temp > PRE_ANNEALING_TARGET_F or max_temp < ANNEALING_END_F:
            return
        y_top = plot_rect.bottom() - (
            (min(PRE_ANNEALING_TARGET_F, max_temp) - min_temp) / span
        ) * plot_rect.height()
        y_bot = plot_rect.bottom() - (
            (max(ANNEALING_END_F, min_temp) - min_temp) / span
        ) * plot_rect.height()
        painter.fillRect(QRectF(plot_rect.left(), y_top, plot_rect.width(), y_bot - y_top), ANNEAL_BAND_COLOR)

    def _draw_line(self, painter: QPainter, to_xy) -> None:
        path = QPainterPath()
        x0, y0 = to_xy(self._points[0])
        path.moveTo(x0, y0)
        for p in self._points[1:]:
            x, y = to_xy(p)
            path.lineTo(x, y)

        pen = QPen(LINE_COLOR)
        pen.setWidthF(2.5)
        painter.setPen(pen)
        painter.drawPath(path)

        if self._current_segment is not None:
            pen = QPen(CURRENT_SEGMENT_COLOR)
            pen.setWidthF(4.0)
            painter.setPen(pen)
            seg_points = [p for p in self._points if p.segment_index == self._current_segment]
            if len(seg_points) >= 2:
                seg_path = QPainterPath()
                x, y = to_xy(seg_points[0])
                seg_path.moveTo(x, y)
                for p in seg_points[1:]:
                    x, y = to_xy(p)
                    seg_path.lineTo(x, y)
                painter.drawPath(seg_path)


def margin_or(value: float) -> float:
    return max(value - 4, 10)
