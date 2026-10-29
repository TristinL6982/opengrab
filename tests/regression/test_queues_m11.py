import pytest

from opengrab.queues import DEFAULT_QUEUE, QueueManager, clean_limit, clean_name


def test_clean_name_and_limit():
    assert clean_name("  Night   jobs ") == "Night jobs"
    assert clean_limit("4") == 4
    with pytest.raises(ValueError):
        clean_name("   ")
    with pytest.raises(ValueError):
        clean_limit(0)
    with pytest.raises(ValueError):
        clean_limit("many")


def test_default_queue_and_create():
    manager = QueueManager(default_max_active=2)
    assert manager.names() == [DEFAULT_QUEUE]
    assert manager.get("main").max_active == 2
    manager.create("Videos", max_active=1)
    assert manager.names() == [DEFAULT_QUEUE, "Videos"]
    with pytest.raises(ValueError):
        manager.create("videos")
    with pytest.raises(KeyError):
        manager.get("Music")


def test_add_move_and_queue_of():
    manager = QueueManager()
    manager.create("Videos")
    assert manager.add("a") == DEFAULT_QUEUE
    assert manager.add("b", queue="videos") == "Videos"
    with pytest.raises(ValueError):
        manager.add("a", queue="Videos")
    assert manager.move("a", "Videos") == "Videos"
    assert manager.get("Videos").items == ["b", "a"]
    assert manager.queue_of("a") == "Videos"
    assert manager.queue_of("zzz") is None
    with pytest.raises(KeyError):
        manager.move("zzz", "Videos")


def test_runnable_respects_per_queue_limits_and_pause():
    manager = QueueManager(default_max_active=2)
    manager.create("Videos", max_active=1)
    for item in ["m1", "m2", "m3"]:
        manager.add(item)
    for item in ["v1", "v2"]:
        manager.add(item, queue="Videos")
    assert manager.runnable() == ["m1", "m2", "v1"]
    manager.start("m1")
    manager.start("v1")
    assert manager.runnable() == ["m2"]
    manager.finish("v1")
    assert manager.runnable() == ["m2", "v2"]
    manager.pause("Videos")
    assert manager.runnable() == ["m2"]
    manager.pause("Videos", paused=False)
    manager.set_limit("main", 3)
    assert manager.runnable() == ["m2", "m3", "v2"]


def test_finish_and_discard():
    manager = QueueManager()
    manager.add("a")
    manager.start("a")
    assert manager.finish("a") is True
    assert manager.finish("a") is False
    assert manager.get(DEFAULT_QUEUE).active == set()
    with pytest.raises(KeyError):
        manager.start("a")


def test_rename_and_remove_move_items_to_default():
    manager = QueueManager()
    manager.create("Night")
    manager.add("n1", queue="Night")
    manager.add("n2", queue="Night")
    manager.start("n1")
    assert manager.rename("night", "Overnight").name == "Overnight"
    assert manager.names() == [DEFAULT_QUEUE, "Overnight"]
    manager.remove("Overnight")
    assert manager.names() == [DEFAULT_QUEUE]
    assert manager.get(DEFAULT_QUEUE).items == ["n1", "n2"]
    assert manager.get(DEFAULT_QUEUE).active == {"n1"}
    with pytest.raises(ValueError):
        manager.remove(DEFAULT_QUEUE)
    with pytest.raises(ValueError):
        manager.rename(DEFAULT_QUEUE, "Other")


def test_round_trip_skips_bad_entries():
    manager = QueueManager()
    manager.create("Videos", max_active=1)
    manager.add("a")
    manager.add("b", queue="Videos")
    manager.pause("Videos")
    data = manager.to_dict()
    data["queues"] += ["junk", {"name": "", "max_active": 2}, {"name": "Bad", "max_active": 0}]
    data["queues"].append({"name": "Extra", "max_active": 2, "items": ["a", 5, "c"]})
    loaded = QueueManager.from_dict(data)
    assert loaded.names() == [DEFAULT_QUEUE, "Videos", "Extra"]
    assert loaded.get("Videos").paused is True
    assert loaded.get("Videos").max_active == 1
    assert loaded.get("Extra").items == ["c"]
    assert loaded.queue_of("a") == DEFAULT_QUEUE
    assert QueueManager.from_dict("junk").names() == [DEFAULT_QUEUE]