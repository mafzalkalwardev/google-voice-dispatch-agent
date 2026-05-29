from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path

from selenium.common.exceptions import WebDriverException

from src.call_log import CallLogger
from src.call_session import CallSession, CallState
from src.config import Config
from src.contacts import load_contacts
from src.google_voice import GoogleVoiceBrowser
from src.ai_groq import GroqAgent
from src.logger import setup_logger
from src.tts import save_text_to_speech, ensure_audio_dir
from src.voice_playback import play_wav_loopback, find_playable_loopback_device, print_devices
from src.paths import runtime_base

BASE_DIR = runtime_base()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Google Voice Dispatch Sales Agent")
    p.add_argument("--contacts", help="Path to Excel or CSV contact list")
    p.add_argument("--profile", default=None, help="Chrome profile name")
    p.add_argument("--objective", default="qualify the carrier and book a quick dispatch onboarding call",
                   help="Call objective sent to AI script generator")
    p.add_argument("--offer", default="dedicated dispatch, high-paying load search, rate negotiation, paperwork support, and 24/7 road support across 48 states",
                   help="Offer summary used in voicemail generation")
    p.add_argument("--callback-number", default=None,
                   help="Callback number spoken in generated voicemails")
    p.add_argument("--agent-name", default=None,
                   help="Agent name spoken in generated scripts")
    p.add_argument("--company-name", default=None,
                   help="Company name spoken in generated scripts")
    p.add_argument("--company-context", default=None,
                   help="Company details used by the AI script generator")
    p.add_argument("--limit", type=int, default=10, help="Maximum calls to attempt")
    p.add_argument("--output-dir", default="audio", help="Directory for generated audio files")
    p.add_argument("--loopback-device", default=None,
                   help="Name hint for virtual audio cable (default from config)")
    p.add_argument("--call-timeout", type=int, default=None,
                   help="Seconds to wait for call connection (default from config)")
    p.add_argument("--call-max-duration", type=int, default=None,
                   help="Max seconds to poll for call state (default from config)")
    p.add_argument("--headless", action="store_true",
                   help="Launch Chrome headless (not recommended for Google Voice)")
    p.add_argument("--dry-run", action="store_true",
                   help="Generate scripts and audio only — skip all dialing")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--realtime", dest="realtime", action="store_true", default=True,
                      help="Use realtime conversation loop (default)")
    mode.add_argument("--static-playback", dest="realtime", action="store_false",
                      help="Use pregenerated WAV playback instead of realtime conversation")
    p.add_argument("--capture-device", default=None,
                   help="Audio capture device hint for realtime mode (default from config)")
    p.add_argument("--tts-voice", default=None,
                   help="edge-tts voice name for realtime mode (default from config)")
    p.add_argument("--list-audio-devices", action="store_true",
                   help="Print sounddevice audio devices and exit")
    p.add_argument("--preflight", action="store_true",
                   help="Run readiness checks and exit without dialing")
    p.add_argument("--audio-route-test", action="store_true",
                   help="Play a short test phrase to the configured loopback output without dialing")
    p.add_argument("--safe-test", metavar="PHONE",
                   help="Safe one-number test mode: run preflight, confirm, then dial exactly "
                        "this number once. Does not read from --contacts.")
    p.add_argument("--diagnose-call-state", metavar="PHONE",
                   help="DOM diagnostic mode: dial PHONE, capture DOM snapshots every 1.5s "
                        "under logs/diagnostics/, hang up after 90s. "
                        "Use only with the designated test number.")
    return p.parse_args()


def _run_preflight(cfg: Config) -> int:
    from src.preflight import run_all

    results = run_all(
        groq_api_key=cfg.groq_api_key,
        contacts_file=cfg.contacts_file,
        profile_name=cfg.profile_name,
        loopback_device=cfg.loopback_device,
        capture_device=cfg.capture_device,
    )
    worst = 0
    for result in results:
        status = result.status.upper()
        if result.status == "fail":
            worst = 1
        print(f"[{status:4}] {result.name}: {result.message}")
    return worst


def _run_audio_route_test(args: argparse.Namespace, cfg: Config) -> int:
    loopback_device = args.loopback_device or cfg.loopback_device
    output_dir = ensure_audio_dir(args.output_dir) / "diagnostics"
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / f"audio_route_test_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    phrase = (
        "Indus Transports audio route test. "
        "If Google Voice microphone is set to CABLE Output, this audio reaches the call."
    )

    device_index = find_playable_loopback_device(loopback_device)
    if device_index is None:
        print(f"[FAIL] No playable loopback output device found for '{loopback_device}'.")
        return 1

    try:
        save_text_to_speech(phrase, wav_path)
        duration = play_wav_loopback(
            wav_path,
            device_hint=loopback_device,
            fallback_to_default=False,
        )
    except Exception as exc:
        print(f"[FAIL] Audio route test failed: {exc}")
        return 1

    print(f"[OK  ] Played {duration:.1f}s to loopback device '{loopback_device}'.")
    print(f"[INFO] Test WAV saved at: {wav_path}")
    return 0


def _run_call(
    contact: dict,
    index: int,
    browser: GoogleVoiceBrowser | None,
    ai: GroqAgent,
    call_logger: CallLogger,
    logger: logging.Logger,
    script_dir: Path,
    voicemail_dir: Path,
    objective: str,
    offer: str,
    callback_number: str,
    agent_name: str,
    company_name: str,
    company_context: str,
    company_website: str,
    loopback_device: str,
    loopback_device_index: int | None,
    loopback_available: bool,
    call_timeout: int,
    call_max_duration: int,
    dry_run: bool,
    realtime: bool = False,
    capture_device: str = "default",
    tts_voice: str = "en-US-GuyNeural",
    stt_model: str = "whisper-large-v3-turbo",
    vad_threshold: float = 0.015,
    groq_api_key: str = "",
    answered_speak_delay: float = 4.0,
    wait_for_human_audio: bool = True,
    human_audio_timeout: float = 8.0,
    answer_confirm_polls: int = 2,
    stt_retry_count: int = 2,
    vad_silence_frames: int = 12,
    vad_speech_frames: int = 2,
) -> None:
    phone = contact["phone"]
    name = contact["name"]
    session = CallSession(phone=phone, contact_name=name)

    logger.info("[%d] Preparing AI audio assets for %s (%s)", index, name, phone)
    script_path: Path | None = None
    opening_line: str | None = None
    if not realtime:
        script_text = ai.generate_call_script(
            contact_name=name,
            objective=objective,
            context=f"Contact: {name}, Phone: {phone}",
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context,
            company_website=company_website,
        )
        script_path = script_dir / f"script_{index}_{phone.replace('+', '')}.wav"
        script_text_path = script_path.with_suffix(".txt")
        script_text_path.write_text(script_text, encoding="utf-8")
        save_text_to_speech(script_text, script_path)
    else:
        opening_line = _generate_realtime_opening_line(
            contact_name=name,
            groq_api_key=groq_api_key,
            groq_model=ai.model,
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context,
            company_website=company_website,
            callback_number=callback_number,
            logger=logger,
            index=index,
        )

    voicemail_text = ai.generate_voicemail(
        contact_name=name,
        offer_summary=offer,
        callback_number=callback_number,
        agent_name=agent_name,
        company_name=company_name,
        company_context=company_context,
    )

    voicemail_path = voicemail_dir / f"voicemail_{index}_{phone.replace('+', '')}.wav"
    voicemail_text_path = voicemail_path.with_suffix(".txt")

    voicemail_text_path.write_text(voicemail_text, encoding="utf-8")

    save_text_to_speech(voicemail_text, voicemail_path)
    if realtime:
        logger.info("[%d] Realtime mode ready; voicemail fallback: %s", index, voicemail_path.name)
    else:
        logger.info("[%d] Static audio ready: %s | %s", index, script_path.name, voicemail_path.name)

    if dry_run:
        logger.info("[%d] DRY RUN — skipping dial for %s", index, phone)
        session.outcome = "DRY_RUN"
        call_logger.log_session(session, notes="dry-run mode")
        return

    # ---- Dial ----
    logger.info("[%d] Dialing %s...", index, phone)
    session.transition(CallState.DIALING)
    dialed = browser.dial_number(phone, connect_timeout=call_timeout)
    if not dialed:
        session.transition(CallState.FAILED, "dial_number returned False")
        session.outcome = "DIAL_FAILED"
        call_logger.log_session(session)
        _archive_call_result(session, contact, {}, logger, index)
        logger.warning("[%d] Dial failed for %s", index, phone)
        return

    # ---- Poll for ANSWERED or VOICEMAIL ----
    # Google Voice shows a hangup button while an outbound call is only ringing,
    # so this wait must use real answer evidence and the shorter answer timeout.
    final_state = browser.detect_call_state(
        session,
        timeout=float(call_timeout),
        ctrl_confirm_polls=answer_confirm_polls,
    )

    if final_state == CallState.CONNECTED:
        if realtime and loopback_device_index is not None:
            logger.info("[%d] Call connected — starting realtime conversation loop", index)
            _run_realtime_loop(
                session=session,
                contact_name=name,
                groq_api_key=groq_api_key,
                groq_model=ai.model,
                agent_name=agent_name,
                company_name=company_name,
                company_context=company_context,
                company_website=company_website,
                callback_number=callback_number,
                loopback_device_index=loopback_device_index,
                capture_device=capture_device,
                tts_voice=tts_voice,
                stt_model=stt_model,
                vad_threshold=vad_threshold,
                opening_line=opening_line,
                browser=browser,
                call_max_duration=call_max_duration,
                logger=logger,
                answered_speak_delay=answered_speak_delay,
                wait_for_human_audio=wait_for_human_audio,
                human_audio_timeout=human_audio_timeout,
                stt_retry_count=stt_retry_count,
                vad_silence_frames=vad_silence_frames,
                vad_speech_frames=vad_speech_frames,
            )
        else:
            logger.info("[%d] Call connected — playing script audio", index)
            if script_path is None:
                raise RuntimeError("Static playback requested but script audio was not generated")
            if not _play_audio(script_path, loopback_device, loopback_available, logger):
                session.transition(CallState.FAILED, "script audio playback failed")
                if browser.is_call_active():
                    browser.hangup_call()
                session.outcome = session.state.value
                call_logger.log_session(session)
                _archive_call_result(session, contact, {}, logger, index)
                return
            # Wait for natural end (up to 60s after audio finishes)
            followup_state = browser.detect_call_state(
                session, timeout=60.0, ctrl_confirm_polls=answer_confirm_polls,
            )
            if followup_state == CallState.VOICEMAIL:
                logger.info("[%d] Voicemail detected after initial connection", index)
                browser.wait_for_voicemail_beep(timeout=30.0)
                if not _play_audio(voicemail_path, loopback_device, loopback_available, logger):
                    session.transition(CallState.FAILED, "voicemail audio playback failed")
                browser.hangup_call()
                if not session.is_terminal():
                    session.transition(CallState.ENDED, "hung up after voicemail playback")
            elif followup_state == CallState.CONNECTED:
                logger.info("[%d] Call still active after playback - hanging up", index)
                browser.hangup_call()
                if not session.is_terminal():
                    session.transition(CallState.ENDED, "hung up after script playback")

    elif final_state == CallState.VOICEMAIL:
        logger.info("[%d] Voicemail detected — playing voicemail audio", index)
        browser.wait_for_voicemail_beep(timeout=30.0)
        if not _play_audio(voicemail_path, loopback_device, loopback_available, logger):
            session.transition(CallState.FAILED, "voicemail audio playback failed")
        browser.hangup_call()
        if not session.is_terminal():
            session.transition(CallState.ENDED, "hung up after voicemail playback")

    elif final_state in (CallState.ENDED, CallState.FAILED):
        logger.info("[%d] Call ended/failed before playback for %s", index, phone)
        if final_state == CallState.FAILED and browser.is_call_active():
            browser.hangup_call()

    session.outcome = session.state.value
    call_logger.log_session(session)
    logger.info(
        "[%d] %s done — state=%s total=%.1fs connected=%.1fs",
        index, phone,
        session.state.value,
        session.total_duration_seconds() or 0.0,
        session.connected_duration_seconds() or 0.0,
    )

    if realtime:
        _extract_and_upsert_lead(
            session=session,
            contact=contact,
            groq_api_key=groq_api_key,
            model=ai.model,
            logger=logger,
            index=index,
        )
    else:
        _archive_call_result(session, contact, {}, logger, index)


def _extract_and_upsert_lead(
    session: CallSession,
    contact: dict,
    groq_api_key: str,
    model: str,
    logger: logging.Logger,
    index: int,
) -> None:
    """Extract structured lead data and archive the call into the CRM store."""
    lead: dict = {}
    try:
        if session.transcript_path and groq_api_key:
            from src.leads import extract_lead_from_transcript, upsert_lead  # type: ignore

            lead = extract_lead_from_transcript(
                transcript_path=session.transcript_path,
                contact=contact,
                groq_api_key=groq_api_key,
                model=model,
            )
            lead["phone_number"] = session.phone
            lead["contact_name"] = lead.get("contact_name") or session.contact_name
            lead["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            lead["transcript_file"] = str(session.transcript_path)
            if not lead.get("call_outcome"):
                lead["call_outcome"] = session.outcome or session.state.value
            upsert_lead(lead)
            logger.info("[%d] Lead upserted for %s", index, session.phone)
    except Exception as exc:
        logger.warning("[%d] Lead extraction error: %s", index, exc)
    _archive_call_result(
        session=session,
        contact=contact,
        lead=lead,
        logger=logger,
        index=index,
        groq_api_key=groq_api_key,
        model=model,
    )


def _archive_call_result(
    session: CallSession,
    contact: dict,
    lead: dict,
    logger: logging.Logger,
    index: int,
    groq_api_key: str = "",
    model: str = "llama-3.3-70b-versatile",
) -> None:
    """Persist call artifacts in connected/voicemail/failed storage and CRM tables."""
    try:
        from src.crm import finalize_call_session  # type: ignore

        result = finalize_call_session(
            session=session,
            contact=contact,
            lead=lead,
            groq_api_key=groq_api_key,
            model=model,
        )
        logger.info(
            "[%d] CRM archive: type=%s stored=%s call_id=%s carrier_id=%s",
            index,
            result.get("call_type", ""),
            result.get("stored", False),
            result.get("call_id", ""),
            result.get("carrier_id", ""),
        )
    except Exception as exc:
        logger.warning("[%d] CRM archive error: %s", index, exc)


def _fallback_opening_line(agent_name: str, company_name: str) -> str:
    return f"Hi, this is {agent_name} with {company_name}, calling about freight dispatch."


def _generate_realtime_opening_line(
    contact_name: str,
    groq_api_key: str,
    groq_model: str,
    agent_name: str,
    company_name: str,
    company_context: str,
    company_website: str,
    callback_number: str,
    logger: logging.Logger,
    index: int,
) -> str:
    """Prepare the first spoken line before dialing so pickup is not silent."""
    fallback = _fallback_opening_line(agent_name, company_name)
    try:
        from src.conversation_agent import ConversationAgent

        logger.info("[%d] Opening line generation started before dialing", index)
        agent = ConversationAgent(
            api_key=groq_api_key,
            model=groq_model,
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context,
            company_website=company_website,
            callback_number=callback_number,
            contact_name=contact_name,
        )
        line = agent.opening_line()
    except Exception as exc:
        logger.error("[%d] Opening line generation failed: %s", index, exc)
        line = ""

    if not line:
        line = fallback
        logger.warning("[%d] Opening line empty; using fallback: %s", index, line)
    else:
        logger.info("[%d] Opening line generated before dialing: %s", index, line)
    return line


def _run_realtime_loop(
    session: CallSession,
    contact_name: str,
    groq_api_key: str,
    groq_model: str,
    agent_name: str,
    company_name: str,
    company_context: str,
    company_website: str,
    callback_number: str,
    loopback_device_index: int,
    capture_device: str,
    tts_voice: str,
    stt_model: str,
    vad_threshold: float,
    opening_line: str | None,
    browser: GoogleVoiceBrowser,
    call_max_duration: int,
    logger: logging.Logger,
    answered_speak_delay: float = 4.0,
    wait_for_human_audio: bool = True,
    human_audio_timeout: float = 8.0,
    stt_retry_count: int = 2,
    vad_silence_frames: int = 12,
    vad_speech_frames: int = 2,
) -> None:
    from src.conversation_agent import ConversationAgent
    from src.conversation_loop import ConversationLoop
    from src.realtime_tts import RealtimeTTS, validate_tts_output_device
    from src.stt import GroqWhisperSTT
    from src.vad import VADConfig
    from src.voice_playback import describe_audio_device

    # Build transcript path: logs/transcripts/<phone>_<timestamp>.txt
    # The logs/ directory is git-ignored; transcripts are never committed.
    transcript_ts = time.strftime("%Y%m%d_%H%M%S")
    safe_phone = session.phone.replace("+", "").replace(" ", "")
    transcript_path = BASE_DIR / "logs" / "transcripts" / f"{safe_phone}_{transcript_ts}.txt"
    recording_path = BASE_DIR / "logs" / "recordings" / f"{safe_phone}_{transcript_ts}.wav"
    session.transcript_path = transcript_path
    session.recording_path = recording_path

    try:
        validate_tts_output_device(loopback_device_index)
        logger.info("Realtime selected output device: %s", describe_audio_device(loopback_device_index))
        logger.info("Realtime selected capture device: CAPTURE_DEVICE='%s'", capture_device)
        tts = RealtimeTTS(device_index=loopback_device_index, voice=tts_voice)
        agent = ConversationAgent(
            api_key=groq_api_key,
            model=groq_model,
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context,
            company_website=company_website,
            callback_number=callback_number,
            contact_name=contact_name,
        )
        stt = GroqWhisperSTT(api_key=groq_api_key, model=stt_model, retry_count=stt_retry_count)
        vad_cfg = VADConfig(
            speech_threshold=vad_threshold,
            silence_trigger_frames=vad_silence_frames,
            speech_trigger_frames=vad_speech_frames,
        )
        loop = ConversationLoop(
            capture_device_hint=capture_device,
            tts=tts,
            agent=agent,
            stt=stt,
            vad_config=vad_cfg,
            transcript_path=transcript_path,
            recording_path=recording_path,
            answered_speak_delay=answered_speak_delay,
            wait_for_human_audio=wait_for_human_audio,
            human_audio_timeout=human_audio_timeout,
        )
        logger.info("Transcript will be saved to: %s", transcript_path)
        logger.info("Recording will be saved to: %s", recording_path)
    except Exception as exc:
        logger.error("Realtime setup error: %s", exc)
        if browser.is_call_active():
            browser.hangup_call()
        if not session.is_terminal():
            session.transition(CallState.FAILED, f"realtime setup error: {exc}")
        return
    monitor_stop = threading.Event()
    monitor = threading.Thread(
        target=_monitor_live_call,
        args=(browser, loop, session, monitor_stop, float(call_max_duration), logger),
        daemon=True,
        name="GoogleVoiceCallMonitor",
    )
    monitor.start()
    try:
        # Brief pause to let call UI stabilize; answer detection runs inside loop.run()
        time.sleep(0.5)
        loop.run(session=session, opening_line=opening_line, auto_opening=True)
    except Exception as exc:
        logger.error("Realtime loop error: %s", exc)
        if not session.is_terminal():
            session.transition(CallState.FAILED, f"realtime loop error: {exc}")
    finally:
        monitor_stop.set()
        monitor.join(timeout=2.0)
        if not session.is_terminal():
            if browser.is_call_active():
                browser.hangup_call()
                session.transition(CallState.ENDED, "realtime loop stopped; hung up active call")
            else:
                session.transition(CallState.ENDED, "Google Voice call ended")


def _monitor_live_call(
    browser: GoogleVoiceBrowser,
    loop,
    session: CallSession,
    stop_event: threading.Event,
    max_duration: float,
    logger: logging.Logger,
) -> None:
    started = time.monotonic()
    inactive_hits = 0
    while not stop_event.is_set() and not loop.is_stopped():
        if max_duration > 0 and time.monotonic() - started >= max_duration:
            logger.warning("Realtime call max duration reached; ending call.")
            if browser.is_call_active():
                browser.hangup_call()
            loop.stop()
            return

        if browser.is_call_active():
            inactive_hits = 0
        else:
            inactive_hits += 1
            if time.monotonic() - started > 3.0 and inactive_hits >= 3:
                logger.info("Google Voice call no longer active; stopping realtime loop.")
                if not session.is_terminal():
                    session.transition(CallState.ENDED, "Google Voice call ended")
                loop.stop()
                return

        time.sleep(1.0)


def _play_audio(
    path: Path,
    device_hint: str,
    loopback_available: bool,
    logger: logging.Logger,
) -> bool:
    if loopback_available:
        try:
            duration = play_wav_loopback(path, device_hint=device_hint, fallback_to_default=False)
            logger.info("Audio played: %.1fs via loopback", duration)
            return True
        except Exception as exc:
            logger.error("Audio playback failed: %s", exc)
            return False
    else:
        logger.warning(
            "No loopback device — audio not injected. File: %s", path
        )
        # sleep rough duration so call isn't cut short
        time.sleep(30)
        return False


def _run_call_state_diagnostic(args: argparse.Namespace, cfg: Config) -> None:
    """
    Dial PHONE, capture DOM snapshots every 1.5 s to logs/diagnostics/, hang up after 90 s.
    Shows what Google Voice DOM looks like in each phase so we can verify CONNECTED signals.
    Only use with the designated safe test number (+17085681794).

    Usage:
        python -m src.main --diagnose-call-state +17085681794
    """
    import json
    import sys

    phone = args.diagnose_call_state.strip()
    diag_dir = BASE_DIR / "logs" / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("  CALL-STATE DIAGNOSTIC — INDUS TRANSPORTS LLC")
    print("=" * 60)
    print(f"\n  Target: {phone}")
    print(f"  Snapshots → {diag_dir}")
    print("\n  This will place a LIVE call. Answer on the other phone,")
    print("  stay silent 5s, then speak normally for 10s, then hang up.")
    print("  The diagnostic will capture DOM state at each transition.")
    print()
    try:
        answer = input("Type YES to proceed: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return
    if answer != "YES":
        print("Aborted.")
        return

    logger = setup_logger()
    profile_name = args.profile or cfg.profile_name

    browser = GoogleVoiceBrowser(profile_name=profile_name, headless=False)
    logger.info("[DIAG] Launching Chrome for diagnostic call to %s", phone)
    browser.launch()

    if not browser.is_logged_in():
        logger.warning("[DIAG] Not logged in — please sign in manually.")
        if not browser.wait_for_manual_login(timeout=300):
            browser.close()
            raise SystemExit("Login timed out.")

    snapshots: list[dict] = []
    phase = "PRE_DIAL"
    snap_idx = 0

    def save_snap(p: str) -> None:
        nonlocal snap_idx
        snap = browser.take_dom_snapshot(p)
        snap["snap_idx"] = snap_idx
        snapshots.append(snap)
        fname = diag_dir / f"snap_{snap_idx:03d}_{p}.json"
        fname.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        logger.info(
            "[DIAG] %s snap #%d — buttons=%d answered_controls=%s timer=%s call_active=%s",
            p, snap_idx,
            len(snap["buttons"]),
            snap["answered_controls_found"],
            snap["call_timer_found"],
            snap["call_active_found"],
        )
        snap_idx += 1

    try:
        save_snap(phase)

        logger.info("[DIAG] Dialing %s ...", phone)
        print(f"\n  >>> Dialing {phone} now. Answer on the other phone.")
        dialed = browser.dial_number(phone, connect_timeout=30)
        if not dialed:
            logger.error("[DIAG] dial_number returned False — aborting.")
            return
        phase = "DIALING"
        save_snap(phase)

        # Poll for up to 90 s, saving a snapshot every 1.5 s
        deadline = time.time() + 90.0
        last_phase = phase
        while time.time() < deadline:
            time.sleep(1.5)

            # Determine observed phase from DOM
            if browser._connected_timer_present():
                phase = "CONNECTED_TIMER"
            elif browser._answered_controls_present()[0]:
                ctrl_labels = browser._answered_controls_present()[1]
                phase = "CONNECTED_CONTROLS"
                logger.info("[DIAG] Answered controls: %s", ctrl_labels)
            elif browser._voicemail_cue_present() or browser._page_contains_voicemail():
                phase = "VOICEMAIL"
            elif browser._any_present("call_ended_banner"):
                phase = "ENDED"
            elif browser._any_present("call_active"):
                phase = "RINGING_OR_CONNECTED"
            else:
                phase = "UNKNOWN_OR_ENDED"

            save_snap(phase)

            if phase in ("ENDED", "VOICEMAIL", "UNKNOWN_OR_ENDED") and last_phase not in (
                "PRE_DIAL", "DIALING"
            ):
                logger.info("[DIAG] Call appears ended — stopping polling.")
                break
            last_phase = phase

    finally:
        phase = "HANGUP"
        save_snap(phase)
        logger.info("[DIAG] Hanging up.")
        browser.hangup_call()
        time.sleep(1)
        save_snap("POST_HANGUP")
        browser.close()

    # Summary
    print("\n" + "=" * 60)
    print(f"  Diagnostic complete — {snap_idx} snapshots saved to:")
    print(f"  {diag_dir}")
    print()
    connected_snaps = [s for s in snapshots if "CONNECTED" in s["phase"]]
    ringing_snaps = [s for s in snapshots if "RINGING" in s["phase"]]
    print(f"  RINGING snapshots: {len(ringing_snaps)}")
    print(f"  CONNECTED snapshots: {len(connected_snaps)}")
    if connected_snaps:
        best = connected_snaps[0]
        print(f"  First CONNECTED at snap #{best['snap_idx']}: "
              f"timer={best['call_timer_found']} "
              f"controls={best['answered_controls_found']}")
    print("=" * 60)


def _run_safe_test(args: argparse.Namespace, cfg: "Config") -> None:
    """
    Safe one-number test mode.

    1. Runs all preflight checks and prints results.
    2. Shows the target number prominently.
    3. Requires explicit keyboard confirmation before dialing.
    4. Dials exactly that one number — ignores --contacts and --limit.
    5. Uses the same realtime pipeline as a live run.

    Usage:
        python -m src.main --safe-test +15551234567
    """
    import sys

    phone = args.safe_test.strip()
    if not phone.startswith("+"):
        print(f"\n[WARN] Number '{phone}' has no country code (+1...). "
              "Google Voice works best with E.164 format.")

    # --- Preflight ---
    print("\n" + "=" * 60)
    print("  SAFE TEST MODE — INDUS TRANSPORTS LLC")
    print("=" * 60)
    print(f"\n  Target number: {phone}\n")
    print("Running preflight checks first...\n")
    exit_code = _run_preflight(cfg)
    if exit_code != 0:
        raise SystemExit(
            "\nPreflight FAILED. Fix the issues above before running a live test."
        )
    print("\nAll preflight checks passed.\n")

    # --- Explicit confirmation ---
    print("=" * 60)
    print(f"  ABOUT TO DIAL: {phone}")
    print("  This is a LIVE call. The number will ring immediately.")
    print("=" * 60)
    try:
        answer = input("\nType YES to confirm and dial, or anything else to abort: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return

    if answer != "YES":
        print("Aborted — you did not type YES.")
        return

    # --- Setup ---
    cfg.validate()
    logger = setup_logger()
    call_logger = CallLogger()

    profile_name    = args.profile or cfg.profile_name
    loopback_device = args.loopback_device or cfg.loopback_device
    callback_number = args.callback_number or cfg.callback_number
    agent_name      = args.agent_name or cfg.agent_name
    company_name    = args.company_name or cfg.company_name
    company_context = args.company_context or cfg.company_context
    company_website = cfg.company_website
    call_timeout    = args.call_timeout or cfg.call_timeout
    call_max_duration = args.call_max_duration or cfg.call_max_duration
    capture_device  = args.capture_device or cfg.capture_device
    tts_voice       = args.tts_voice or cfg.tts_voice

    if not callback_number:
        raise SystemExit(
            "CALLBACK_NUMBER is not configured. Add it to .env or pass --callback-number."
        )

    ai = GroqAgent(api_key=cfg.groq_api_key, model=cfg.groq_model)
    output_dir   = ensure_audio_dir(args.output_dir)
    script_dir   = output_dir / "scripts"
    voicemail_dir = output_dir / "voicemails"
    script_dir.mkdir(parents=True, exist_ok=True)
    voicemail_dir.mkdir(parents=True, exist_ok=True)

    loopback_device_index = find_playable_loopback_device(loopback_device)
    loopback_available    = loopback_device_index is not None
    if not loopback_available:
        logger.warning("Loopback device '%s' not found — audio injection disabled.", loopback_device)
    if args.realtime and not loopback_available:
        raise SystemExit(
            "Realtime mode needs a loopback output device. "
            "Install VB-CABLE or run with --static-playback."
        )

    contact = {"phone": phone, "name": "Safe-Test Contact"}

    browser = GoogleVoiceBrowser(profile_name=profile_name, headless=args.headless)
    logger.info("Launching Chrome for safe test call to %s", phone)
    browser.launch()

    if not browser.is_logged_in():
        logger.warning("Google Voice not logged in — please sign in manually.")
        if not browser.wait_for_manual_login(timeout=300):
            browser.close()
            raise SystemExit("Login timed out.")

    # Warn if Chrome's mic is not CABLE Output — without this Tony's audio never reaches the call.
    _cable_out = loopback_device.replace("Input", "Output").replace("INPUT", "OUTPUT")
    browser.warn_if_mic_not_set(_cable_out)

    try:
        _run_call(
            contact, 1, browser, ai, call_logger, logger,
            script_dir, voicemail_dir,
            args.objective, args.offer,
            callback_number,
            agent_name, company_name, company_context, company_website,
            loopback_device, loopback_device_index, loopback_available,
            call_timeout, call_max_duration,
            dry_run=False,
            realtime=args.realtime,
            capture_device=capture_device,
            tts_voice=tts_voice,
            stt_model=cfg.stt_model,
            vad_threshold=cfg.vad_threshold,
            groq_api_key=cfg.groq_api_key,
            answered_speak_delay=cfg.answered_speak_delay_seconds,
            wait_for_human_audio=cfg.wait_for_human_audio,
            human_audio_timeout=cfg.human_audio_timeout_seconds,
            answer_confirm_polls=cfg.answer_confirm_polls,
            stt_retry_count=cfg.stt_retry_count,
            vad_silence_frames=cfg.vad_silence_frames,
            vad_speech_frames=cfg.vad_speech_frames,
        )
    finally:
        browser.close()

    logger.info("Safe test call completed.")


def main() -> None:
    args = _parse_args()
    if args.list_audio_devices:
        print_devices()
        return

    cfg = Config.load()
    if args.preflight:
        raise SystemExit(_run_preflight(cfg))
    if args.audio_route_test:
        raise SystemExit(_run_audio_route_test(args, cfg))

    # ---- DOM diagnostic mode ----
    if args.diagnose_call_state:
        _run_call_state_diagnostic(args, cfg)
        return

    # ---- Safe one-number test mode ----
    if args.safe_test:
        _run_safe_test(args, cfg)
        return

    cfg.validate()
    logger = setup_logger()
    call_logger = CallLogger()

    contact_path = Path(args.contacts or cfg.contacts_file)
    profile_name = args.profile or cfg.profile_name
    loopback_device = args.loopback_device or cfg.loopback_device
    callback_number = args.callback_number or cfg.callback_number
    agent_name = args.agent_name or cfg.agent_name
    company_name = args.company_name or cfg.company_name
    company_context = args.company_context or cfg.company_context
    company_website = cfg.company_website
    call_timeout = args.call_timeout or cfg.call_timeout
    call_max_duration = args.call_max_duration or cfg.call_max_duration
    capture_device = args.capture_device or cfg.capture_device
    tts_voice = args.tts_voice or cfg.tts_voice

    if not callback_number:
        raise SystemExit(
            "CALLBACK_NUMBER is not configured. Add it to .env or pass --callback-number."
        )

    logger.info("Loading contacts from %s", contact_path)
    contacts = load_contacts(contact_path)
    if not contacts:
        raise SystemExit("No valid contacts loaded — check your contacts file.")
    logger.info("Loaded %d contacts, limit=%d", len(contacts), args.limit)

    ai = GroqAgent(api_key=cfg.groq_api_key, model=cfg.groq_model)

    output_dir = ensure_audio_dir(args.output_dir)
    script_dir = output_dir / "scripts"
    voicemail_dir = output_dir / "voicemails"
    script_dir.mkdir(parents=True, exist_ok=True)
    voicemail_dir.mkdir(parents=True, exist_ok=True)

    loopback_device_index = find_playable_loopback_device(loopback_device)
    loopback_available = loopback_device_index is not None
    if not loopback_available:
        logger.warning(
            "Loopback device '%s' not found — audio injection disabled. "
            "Install VB-CABLE from https://vb-audio.com/Cable/",
            loopback_device,
        )
    if args.realtime and not args.dry_run and not loopback_available:
        raise SystemExit(
            "Realtime mode needs an output loopback device so TTS reaches Google Voice. "
            "Install/configure VB-CABLE, set LOOPBACK_DEVICE or --loopback-device to the "
            "playback cable input, or run with --static-playback."
        )

    if args.dry_run:
        logger.info("=== DRY RUN mode — no browser will be opened ===")
        for i, contact in enumerate(contacts[: args.limit], start=1):
            _run_call(
                contact, i, None, ai, call_logger, logger,
                script_dir, voicemail_dir,
                args.objective, args.offer,
                callback_number,
                agent_name, company_name, company_context, company_website,
                loopback_device, loopback_device_index, loopback_available,
                call_timeout, call_max_duration,
                dry_run=True,
                realtime=args.realtime,
                capture_device=capture_device,
                tts_voice=tts_voice,
                stt_model=cfg.stt_model,
                vad_threshold=cfg.vad_threshold,
                groq_api_key=cfg.groq_api_key,
                answered_speak_delay=cfg.answered_speak_delay_seconds,
                wait_for_human_audio=cfg.wait_for_human_audio,
                human_audio_timeout=cfg.human_audio_timeout_seconds,
                answer_confirm_polls=cfg.answer_confirm_polls,
            stt_retry_count=cfg.stt_retry_count,
            vad_silence_frames=cfg.vad_silence_frames,
            vad_speech_frames=cfg.vad_speech_frames,
            )
        logger.info("Dry run complete. Audio files in %s/", output_dir)
        return

    browser = GoogleVoiceBrowser(profile_name=profile_name, headless=args.headless)
    logger.info("Launching Chrome profile: %s", profile_name)
    browser.launch()

    if not browser.is_logged_in():
        logger.warning(
            "Google Voice is not logged in. Please sign in manually in the opened browser."
        )
        if not browser.wait_for_manual_login(timeout=300):
            browser.close()
            raise SystemExit("Login timed out after 5 minutes.")

    # Warn if Chrome's mic is not CABLE Output — without this Tony's audio never reaches the call.
    _cable_out = loopback_device.replace("Input", "Output").replace("INPUT", "OUTPUT")
    browser.warn_if_mic_not_set(_cable_out)

    logger.info("Logged in. Starting call loop (%d contacts).", min(len(contacts), args.limit))

    try:
        for i, contact in enumerate(contacts[: args.limit], start=1):
            try:
                _run_call(
                    contact, i, browser, ai, call_logger, logger,
                    script_dir, voicemail_dir,
                    args.objective, args.offer,
                    callback_number,
                    agent_name, company_name, company_context, company_website,
                    loopback_device, loopback_device_index, loopback_available,
                    call_timeout, call_max_duration,
                    dry_run=False,
                    realtime=args.realtime,
                    capture_device=capture_device,
                    tts_voice=tts_voice,
                    stt_model=cfg.stt_model,
                    vad_threshold=cfg.vad_threshold,
                    groq_api_key=cfg.groq_api_key,
                    answered_speak_delay=cfg.answered_speak_delay_seconds,
                    wait_for_human_audio=cfg.wait_for_human_audio,
                    human_audio_timeout=cfg.human_audio_timeout_seconds,
                    answer_confirm_polls=cfg.answer_confirm_polls,
            stt_retry_count=cfg.stt_retry_count,
            vad_silence_frames=cfg.vad_silence_frames,
            vad_speech_frames=cfg.vad_speech_frames,
                )
            except WebDriverException as exc:
                logger.error("Chrome/Google Voice session ended; stopping call loop: %s", exc)
                break
            if browser.driver is None:
                logger.error("Chrome/Google Voice session is no longer available; stopping call loop.")
                break
            time.sleep(2)  # brief pause between calls
    finally:
        browser.close()

    logger.info("All calls complete. Logs: logs/call_logs.csv | Audio: %s/", output_dir)


if __name__ == "__main__":
    main()
