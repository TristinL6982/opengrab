"""Dynamic segmentation planner for multi-connection downloads (M11n).

Pure module: plans byte ranges and, when a connection frees up, splits the
remaining range of the slowest active segment (IDM-style). No threads, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_SEGMENT = 1024 * 1024
DEFAULT_CONNECTIONS = 8
MAX_CONNECTIONS = 16


def clamp_connections(value, default: int = DEFAULT_CONNECTIONS) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_CONNECTIONS, number))


@dataclass
class Segment:
    start: int
    end: int
    done: int = 0
    speed: float = 0.0
    active: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def remaining(self) -> int:
        return max(0, self.length - self.done)

    @property
    def next_offset(self) -> int:
        return self.start + self.done

    @property
    def finished(self) -> bool:
        return self.remaining == 0


def initial_segments(size: int, connections, min_size: int = MIN_SEGMENT, ranges: bool = True) -> list:
    size = int(size)
    if size <= 0:
        return []
    count = clamp_connections(connections) if ranges else 1
    count = max(1, min(count, size // max(1, min_size)))
    step = size // count
    segments = []
    for index in range(count):
        start = index * step
        end = size - 1 if index == count - 1 else start + step - 1
        segments.append(Segment(start, end))
    return segments


def pick_split(segments, min_size: int = MIN_SEGMENT):
    candidates = [
        (seg.speed, -seg.remaining, seg.start, index)
        for index, seg in enumerate(segments)
        if seg.active and seg.remaining >= 2 * min_size
    ]
    if not candidates:
        return None
    return min(candidates)[3]


def split_segment(segment: Segment) -> Segment:
    if segment.remaining < 2:
        raise ValueError("segment too small to split")
    boundary = segment.next_offset + segment.remaining // 2
    new = Segment(boundary, segment.end)
    segment.end = boundary - 1
    return new


class SegmentPlanner:
    def __init__(self, size: int, max_connections=DEFAULT_CONNECTIONS, min_size: int = MIN_SEGMENT, ranges: bool = True):
        self.size = int(size)
        self.ranges = bool(ranges)
        self.max_connections = clamp_connections(max_connections) if self.ranges else 1
        self.min_size = max(1, int(min_size))
        self.segments = initial_segments(self.size, self.max_connections, self.min_size, self.ranges)

    def active_count(self) -> int:
        return sum(1 for seg in self.segments if seg.active)

    def next_segment(self):
        """Return a segment for a free connection, or None."""
        if self.active_count() >= self.max_connections:
            return None
        for seg in self.segments:
            if not seg.active and not seg.finished:
                seg.active = True
                return seg
        if not self.ranges:
            return None
        index = pick_split(self.segments, self.min_size)
        if index is None:
            return None
        new = split_segment(self.segments[index])
        new.active = True
        self.segments.insert(index + 1, new)
        return new

    def report(self, segment: Segment, done: int, speed: float = 0.0) -> None:
        segment.done = max(0, min(int(done), segment.length))
        segment.speed = max(0.0, float(speed))

    def release(self, segment: Segment) -> None:
        segment.active = False
        segment.speed = 0.0

    def downloaded(self) -> int:
        return sum(min(seg.done, seg.length) for seg in self.segments)

    def complete(self) -> bool:
        return bool(self.segments) and all(seg.finished for seg in self.segments)