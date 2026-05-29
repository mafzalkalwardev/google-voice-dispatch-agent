from src.audio_routing import captures_tts_loopback, safe_capture_hint


def test_detects_same_vb_cable_capture_as_tts_loopback():
    assert captures_tts_loopback("CABLE Output", "CABLE Input")
    assert safe_capture_hint("CABLE Output", "CABLE Input") == "default"


def test_allows_default_and_second_cable_capture():
    assert not captures_tts_loopback("default", "CABLE Input")
    assert not captures_tts_loopback("CABLE B Output", "CABLE Input")
    assert safe_capture_hint("CABLE B Output", "CABLE Input") == "CABLE B Output"
