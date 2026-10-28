from unittest.mock import patch

from src.audio_capture import AudioCapture


def test_capture_worker_initializes_and_uninitializes_com():
    capture = AudioCapture("default")

    with patch("src.audio_capture._initialize_com_for_capture_thread", return_value=True) as init, \
         patch("src.audio_capture._uninitialize_com_for_capture_thread") as uninit, \
         patch.object(capture, "_run_soundcard_loopback"):
        capture._run()

    init.assert_called_once_with()
    uninit.assert_called_once_with(True)
