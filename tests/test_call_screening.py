from unittest.mock import MagicMock, patch

from src.google_voice import GoogleVoiceBrowser


def _browser() -> GoogleVoiceBrowser:
    b = GoogleVoiceBrowser.__new__(GoogleVoiceBrowser)
    b.driver = MagicMock()
    b.avoid_page_reload = True
    return b


def test_is_call_screening_active_from_page_source():
    b = _browser()
    b.driver.page_source = "<html>Please record your name and reason for calling</html>"
    with patch.object(b, "_any_present", return_value=False):
        assert b.is_call_screening_active() is True


def test_open_calls_page_skips_reload_when_on_calls():
    b = _browser()
    b.driver.current_url = "https://voice.google.com/u/0/calls"
    with patch.object(b, "_find_first", return_value=MagicMock()):
        assert b._open_calls_page() is True
    b.driver.get.assert_not_called()
