import pytest

from opengrab.segments import (
    MAX_CONNECTIONS,
    Segment,
    SegmentPlanner,
    clamp_connections,
    initial_segments,
    pick_split,
    split_segment,
)

MB = 1024 * 1024


def test_clamp_connections():
    assert clamp_connections(0) == 1
    assert clamp_connections(99) == MAX_CONNECTIONS
    assert clamp_connections("4") == 4
    assert clamp_connections("junk") == 8
    assert clamp_connections(None, default=3) == 3


def test_initial_segments_cover_file_exactly():
    segs = initial_segments(10 * MB + 5, 4)
    assert len(segs) == 4
    assert segs[0].start == 0
    assert segs[-1].end == 10 * MB + 4
    for left, right in zip(segs, segs[1:]):
        assert right.start == left.end + 1
    assert sum(seg.length for seg in segs) == 10 * MB + 5


def test_initial_segments_small_file_and_no_ranges():
    assert len(initial_segments(MB // 2, 8)) == 1
    assert len(initial_segments(3 * MB, 8)) == 3
    assert len(initial_segments(100 * MB, 8, ranges=False)) == 1
    assert initial_segments(0, 8) == []


def test_split_halves_remaining_range():
    seg = Segment(0, 99, done=20)
    new = split_segment(seg)
    assert seg.end == 59
    assert (new.start, new.end) == (60, 99)
    assert seg.remaining == 40
    assert new.remaining == 40
    with pytest.raises(ValueError):
        split_segment(Segment(0, 9, done=9))


def test_pick_split_prefers_slowest_big_active_segment():
    segs = [
        Segment(0, 10 * MB - 1, active=True, speed=500.0),
        Segment(10 * MB, 20 * MB - 1, active=True, speed=100.0),
        Segment(20 * MB, 21 * MB - 1, active=True, speed=1.0),
        Segment(21 * MB, 40 * MB - 1, active=False, speed=0.0),
    ]
    assert pick_split(segs, MB) == 1
    assert pick_split([Segment(0, MB, active=True)], MB) is None


def test_planner_hands_out_then_splits_slowest():
    planner = SegmentPlanner(8 * MB, max_connections=2, min_size=MB)
    first = planner.next_segment()
    second = planner.next_segment()
    assert planner.next_segment() is None
    planner.report(first, first.length, speed=900.0)
    planner.release(first)
    planner.report(second, MB, speed=50.0)
    expected_start = second.next_offset + second.remaining // 2
    third = planner.next_segment()
    assert third.start == expected_start
    assert third.end == 8 * MB - 1
    assert second.end == third.start - 1
    assert planner.active_count() == 2
    assert sum(seg.length for seg in planner.segments) == 8 * MB


def test_planner_single_connection_without_ranges_and_completion():
    planner = SegmentPlanner(5 * MB, max_connections=8, min_size=MB, ranges=False)
    seg = planner.next_segment()
    assert planner.next_segment() is None
    assert planner.complete() is False
    planner.report(seg, 10 * MB)
    planner.release(seg)
    assert planner.downloaded() == 5 * MB
    assert planner.complete() is True
    assert planner.next_segment() is None