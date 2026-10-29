import pytest

from opengrab.pairing import (
    CODE_LENGTH,
    PairingError,
    PairingStore,
    clean_name,
    format_code,
    hash_token,
    normalize_code,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_store(codes=("ABCD2345", "WXYZ6789"), tokens=("tok-one", "tok-two", "tok-three")):
    clock = Clock()
    code_iter = iter(codes)
    token_iter = iter(tokens)
    store = PairingStore(clock=clock, code_factory=lambda: next(code_iter),
                         token_factory=lambda: next(token_iter), code_ttl=300)
    return store, clock


def test_normalize_and_format_code():
    assert normalize_code("abcd-2345") == "ABCD2345"
    assert normalize_code(" ABCD 2345 ") == "ABCD2345"
    assert format_code("abcd2345") == "ABCD-2345"
    assert CODE_LENGTH == 8


def test_normalize_rejects_bad_codes():
    with pytest.raises(PairingError):
        normalize_code("ABCD234")
    with pytest.raises(PairingError):
        normalize_code("ABCD2340")
    with pytest.raises(PairingError):
        normalize_code(12345678)


def test_claim_creates_device_with_hashed_token():
    store, clock = make_store()
    code = store.start()
    assert code == "ABCD2345"
    assert store.pending_count() == 1
    clock.now = 1010.0
    device, token = store.claim("abcd-2345", "  Phone  ")
    assert token == "tok-one"
    assert device.name == "Phone"
    assert device.token_hash == hash_token("tok-one")
    assert device.id == hash_token("tok-one")[:12]
    assert device.created == 1010.0
    assert "tok-one" not in str(store.to_dict())
    assert store.pending_count() == 0


def test_code_is_single_use():
    store, _ = make_store()
    store.start()
    store.claim("ABCD2345")
    with pytest.raises(PairingError):
        store.claim("ABCD2345")


def test_code_expires():
    store, clock = make_store()
    store.start()
    clock.now = 1300.0
    with pytest.raises(PairingError):
        store.claim("ABCD2345")
    assert store.pending_count() == 0


def test_too_many_wrong_codes_cancel_pending():
    store, _ = make_store()
    store.start()
    for _ in range(4):
        with pytest.raises(PairingError):
            store.claim("ZZZZ2222")
    assert store.pending_count() == 1
    with pytest.raises(PairingError):
        store.claim("not a code")
    assert store.pending_count() == 0
    with pytest.raises(PairingError):
        store.claim("ABCD2345")


def test_start_resets_failed_counter():
    store, _ = make_store()
    store.start()
    for _ in range(4):
        with pytest.raises(PairingError):
            store.claim("ZZZZ2222")
    store.start()
    with pytest.raises(PairingError):
        store.claim("ZZZZ2222")
    assert store.pending_count() == 2


def test_authenticate_and_revoke():
    store, clock = make_store()
    store.start()
    device, token = store.claim("ABCD2345", "Laptop")
    assert store.authenticate("wrong") is None
    assert store.authenticate("") is None
    assert store.authenticate(None) is None
    clock.now = 2000.0
    assert store.authenticate(token) is device
    assert device.last_seen == 2000.0
    assert store.revoke(device.id) is True
    assert store.revoke(device.id) is False
    assert store.authenticate(token) is None


def test_rename_and_public_view():
    store, _ = make_store()
    store.start()
    device, _ = store.claim("ABCD2345", "Old")
    assert store.rename(device.id, "New name") is True
    assert store.rename("missing", "x") is False
    assert device.public() == {"id": device.id, "name": "New name",
                               "created": 1000.0, "last_seen": None}


def test_clean_name():
    assert clean_name(None) == "Device"
    assert clean_name("   ") == "Device"
    assert clean_name("a\nb") == "ab"
    assert len(clean_name("x" * 200)) == 64


def test_round_trip_and_bad_entries():
    store, clock = make_store()
    store.start()
    store.claim("ABCD2345", "One")
    clock.now = 1001.0
    store.start()
    store.claim("WXYZ6789", "Two")
    saved = store.to_dict()
    saved["devices"].append({"name": "broken"})
    other = PairingStore()
    assert other.load_dict(saved) == ["2"]
    assert [d.name for d in other.devices()] == ["One", "Two"]
    assert other.authenticate("tok-two").name == "Two"
    assert other.load_dict("garbage") == []
    assert other.devices() == []