"""M12k: captcha inbox push hook and the 'captcha waiting' notification."""
from opengrab.captcha.inbox import CaptchaInbox
from opengrab.notifications import NotificationCenter, captcha_waiting_notification


def test_on_new_called_once_per_new_request():
    seen = []
    inbox = CaptchaInbox(on_new=seen.append)
    a = inbox.submit("host.example", "image", "https://host.example/f", now=1000.0)
    b = inbox.submit("host.example", "image", "https://host.example/f", now=1001.0)
    assert a is b
    assert seen == [a]
    c = inbox.submit("other.example", "recaptcha", "https://other.example/", now=1002.0)
    assert seen == [a, c]


def test_broken_hook_does_not_block_submit():
    def boom(req):
        raise RuntimeError("push target down")
    inbox = CaptchaInbox(on_new=boom)
    req = inbox.submit("h", "image", "u")
    assert inbox.get(req.id) is req and req.status == "pending"


def test_no_hook_is_default():
    inbox = CaptchaInbox()
    assert inbox.on_new is None
    assert inbox.submit("h", "image", "u").status == "pending"


def test_captcha_waiting_notification_feeds_center():
    kind, title, message = captcha_waiting_notification("rapid.example", "recaptcha")
    assert kind == "info"
    assert "rapid.example" in title
    assert "recaptcha" in message and "paired device" in message
    center = NotificationCenter(toasts_enabled=False)
    note = center.add(kind, title, message)
    assert center.unread_count() == 1 and note.title == title
    assert captcha_waiting_notification("", "")[1] == "Captcha waiting: unknown host"
