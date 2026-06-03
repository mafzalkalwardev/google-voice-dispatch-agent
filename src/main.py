from __future__ import annotations

import argparse
import logging
import signal
import sys
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
from src.audio_routing import safe_capture_hint

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
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Dial every contact in the list; do not skip numbers completed in a prior run.",
    )
    p.add_argument(
        "--reset-batch-progress",
        action="store_true",
        help="Clear saved batch progress for the current contacts file before dialing.",
    )
    return p.parse_args()


def _install_shutdown_handlers(browser_holder: list) -> None:
    def _shutdown(signum: int, frame) -> None:  # noqa: ARG001
        logger = logging.getLogger("GoogleVoiceAgent")
        logger.info("Shutdown signal %s — closing browser", signum)
        browser = browser_holder[0] if browser_holder else None
        if browser is not None:
            try:
                if browser.is_call_active():
                    browser.hangup_call()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _shutdown)


def _scaled_groq_rate_limit(cfg: Config) -> int:
    """Scale client throttle when multiple Groq accounts are configured."""
    return cfg.groq_max_retries_per_minute * max(1, len(cfg.groq_api_keys))


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
    min_ring_seconds: float = 0.0,
    max_ring_seconds: float = 45.0,
    voicemail_detect_seconds: float = 15.0,
    tts_warmup: bool = True,
    tts_allow_sapi_fallback: bool = False,
    silence_does_not_end_call: bool = True,
    use_stt_context: bool = True,
    max_silence_seconds: float = 8.0,
    llm_model_realtime: str = "llama-3.1-8b-instant",
    listen_after_tts_delay_ms: int = 150,
    use_thinking_fillers: bool = True,
    filler_probability: float = 0.7,
    stream_llm_replies: bool = True,
    voicemail_max_wait_seconds: float = 8.0,
    voicemail_play_after_seconds: float = 4.0,
    voicemail_play_on_greeting: bool = True,
    voicemail_greeting_frames_required: int = 6,
    screening_purpose_text: str = "freight dispatch and load support",
    groq_max_retries_per_minute: int = 60,
) -> str | None:
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

    try:
        from src.realtime_tts import save_edge_tts_wav

        save_edge_tts_wav(voicemail_text, voicemail_path, voice=tts_voice)
    except Exception as exc:
        logger.warning("[%d] edge-tts voicemail generation failed (%s); using SAPI fallback", index, exc)
        save_text_to_speech(voicemail_text, voicemail_path)

    # Pre-generate realtime opening line before dialing so pickup is not silent.
    if realtime:
        agent_opening_ok = False
        try:
            opening_line = _generate_realtime_opening_line(
                phone=phone,
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
            agent_opening_ok = bool(opening_line)
        except Exception as exc:
            logger.error("[%d] Opening line generation failed (will use fallback): %s", index, exc)
            agent_opening_ok = False

        if not agent_opening_ok:
            opening_line = _fallback_opening_line(agent_name=agent_name, company_name=company_name)
            logger.warning("[%d] Using fallback static opening line: %s", index, opening_line)

    if realtime:
        logger.info("[%d] Realtime mode ready; voicemail fallback: %s", index, voicemail_path.name)
    else:
        logger.info("[%d] Static audio ready: %s | %s", index, script_path.name, voicemail_path.name)

    if dry_run:
        logger.info("[%d] DRY RUN — skipping dial for %s", index, phone)
        session.outcome = "DRY_RUN"
        call_logger.log_session(session, notes="dry-run mode")
        return None

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
        return phone

    # ---- Poll for ANSWERED or VOICEMAIL ----
    # Google Voice shows a hangup button while an outbound call is only ringing,
    # so this wait must use real answer evidence and the shorter answer timeout.
    final_state = browser.detect_call_state(
        session,
        timeout=float(call_timeout),
        poll_interval=0.5,
        ctrl_confirm_polls=answer_confirm_polls,
        min_ring_seconds=min_ring_seconds,
        max_ring_seconds=max_ring_seconds,
        voicemail_detect_seconds=voicemail_detect_seconds,
    )

    if final_state == CallState.CONNECTED:
        if realtime and loopback_device_index is not None:
            logger.info("[%d] Call connected — starting realtime conversation loop", index)
            from src.opening_pool import pick_opening
            from src.realtime_tts import RealtimeTTS, validate_tts_output_device

            opening_line = pick_opening(
                phone,
                agent_name,
                company_name,
                llm_line=opening_line,
            )
            shared_tts = None
            try:
                validate_tts_output_device(loopback_device_index)
                shared_tts = RealtimeTTS(
                    device_index=loopback_device_index,
                    voice=tts_voice,
                    use_cache=tts_warmup,
                    allow_sapi_fallback=tts_allow_sapi_fallback,
                )
                if loopback_device_index is not None:
                    _handle_call_screening(
                        browser,
                        loopback_device_index,
                        agent_name,
                        company_name,
                        screening_purpose_text,
                        logger,
                        tts_voice=tts_voice,
                        tts=shared_tts,
                        allow_sapi_fallback=tts_allow_sapi_fallback,
                    )
                if opening_line:
                    shared_tts.prewarm_line(opening_line)
            except Exception as exc:
                logger.warning("[%d] Shared RealtimeTTS setup failed: %s", index, exc)
                shared_tts = None
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
                voicemail_path=voicemail_path,
                browser=browser,
                loopback_device=loopback_device,
                loopback_available=loopback_available,
                call_max_duration=call_max_duration,
                logger=logger,
                answered_speak_delay=answered_speak_delay,
                wait_for_human_audio=wait_for_human_audio,
                human_audio_timeout=human_audio_timeout,
                stt_retry_count=stt_retry_count,
                vad_silence_frames=vad_silence_frames,
                vad_speech_frames=vad_speech_frames,
                voicemail_detect_seconds=voicemail_detect_seconds,
                tts_warmup=tts_warmup,
                silence_does_not_end_call=silence_does_not_end_call,
                use_stt_context=use_stt_context,
                max_silence_seconds=max_silence_seconds,
                llm_model_realtime=llm_model_realtime,
                listen_after_tts_delay_ms=listen_after_tts_delay_ms,
                use_thinking_fillers=use_thinking_fillers,
                filler_probability=filler_probability,
                stream_llm_replies=stream_llm_replies,
                voicemail_play_on_greeting=voicemail_play_on_greeting,
                voicemail_greeting_frames_required=voicemail_greeting_frames_required,
                groq_max_retries_per_minute=groq_max_retries_per_minute,
                shared_tts=shared_tts,
                tts_allow_sapi_fallback=tts_allow_sapi_fallback,
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
                return phone
            # Wait for natural end (up to 60s after audio finishes)
            followup_state = browser.detect_call_state(
                session,
                timeout=60.0,
                poll_interval=0.5,
                ctrl_confirm_polls=answer_confirm_polls,
                min_ring_seconds=min_ring_seconds,
                max_ring_seconds=max_ring_seconds,
                voicemail_detect_seconds=voicemail_detect_seconds,
            )
            if followup_state == CallState.VOICEMAIL:
                logger.info("[%d] Voicemail detected after initial connection", index)
                _deliver_voicemail_and_hangup(
                    browser,
                    session,
                    voicemail_path,
                    loopback_device,
                    loopback_available,
                    logger,
                    max_wait=voicemail_max_wait_seconds,
                    ready_after=voicemail_play_after_seconds,
                )
            elif followup_state == CallState.CONNECTED:
                logger.info("[%d] Call still active after playback - hanging up", index)
                browser.hangup_call()
                if not session.is_terminal():
                    session.transition(CallState.ENDED, "hung up after script playback")

    elif final_state == CallState.VOICEMAIL:
        logger.info("[%d] Voicemail detected — fast voicemail delivery", index)
        _deliver_voicemail_and_hangup(
            browser,
            session,
            voicemail_path,
            loopback_device,
            loopback_available,
            logger,
            max_wait=voicemail_max_wait_seconds,
            ready_after=voicemail_play_after_seconds,
        )

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
            opening_line=opening_line or "",
        )
    else:
        _archive_call_result(session, contact, {}, logger, index)
        try:
            from src.call_intelligence import record_call_from_session

            record_call_from_session(session, contact, lead={})
        except Exception as exc:
            logger.warning("[%d] Call intelligence update failed: %s", index, exc)
    return phone


def _extract_and_upsert_lead(
    session: CallSession,
    contact: dict,
    groq_api_key: str,
    model: str,
    logger: logging.Logger,
    index: int,
    opening_line: str = "",
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
    try:
        from src.call_intelligence import record_call_from_session

        record_call_from_session(session, contact, lead=lead, opening_line=opening_line)
    except Exception as exc:
        logger.warning("[%d] Call intelligence update failed: %s", index, exc)


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
    from src.opening_pool import random_curated

    return random_curated(agent_name, company_name)


def _screening_response_text(agent_name: str, company_name: str, purpose: str) -> str:
    return (
        f"{agent_name} from {company_name}, calling about {purpose}."
    )


def _handle_call_screening(
    browser: GoogleVoiceBrowser,
    loopback_device_index: int,
    agent_name: str,
    company_name: str,
    purpose: str,
    logger: logging.Logger,
    tts_voice: str = "en-US-GuyNeural",
    tts: object | None = None,
    allow_sapi_fallback: bool = False,
) -> bool:
    """Respond to Google Voice name/purpose screening, then wait for live answer."""
    if not browser.is_call_screening_active():
        return False
    logger.info("Call screening detected — speaking name and purpose")
    try:
        from src.realtime_tts import RealtimeTTS

        own_tts = tts is None
        if own_tts:
            tts = RealtimeTTS(
                device_index=loopback_device_index,
                voice=tts_voice,
                use_cache=True,
                allow_sapi_fallback=allow_sapi_fallback,
            )
        line = _screening_response_text(agent_name, company_name, purpose)
        assert tts is not None
        if hasattr(tts, "prewarm_line"):
            tts.prewarm_line(line)  # type: ignore[attr-defined]
        tts.speak(line)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("Screening TTS failed: %s", exc)
        return False
    if browser.wait_for_live_after_screening(timeout=25.0):
        logger.info("Call screening cleared — live call ready")
        return True
    logger.warning("Call screening did not clear within timeout")
    return False


def _deliver_voicemail_and_hangup(
    browser: GoogleVoiceBrowser,
    session: CallSession,
    voicemail_path: Path,
    loopback_device: str,
    loopback_available: bool,
    logger: logging.Logger,
    *,
    max_wait: float = 8.0,
    ready_after: float = 4.0,
) -> bool:
    browser.wait_for_voicemail_ready(max_wait=max_wait, min_ready_after=ready_after)
    if not _play_audio(voicemail_path, loopback_device, loopback_available, logger):
        if not session.is_terminal():
            session.transition(CallState.FAILED, "voicemail audio playback failed")
        return False
    time.sleep(1.0)
    if browser.is_call_active():
        browser.hangup_call()
    if not session.is_terminal():
        session.transition(CallState.ENDED, "hung up after voicemail playback")
    return True


def _generate_realtime_opening_line(
    phone: str,
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
            contact_phone=phone,
        )
        line = agent.opening_line()
    except Exception as exc:
        logger.error("[%d] Opening line generation failed: %s", index, exc)
        line = ""

    from src.opening_pool import pick_opening

    line = pick_opening(
        phone,
        agent_name,
        company_name,
        llm_line=line or None,
    )
    if not line:
        line = fallback
        logger.warning("[%d] Opening line empty; using fallback: %s", index, line)
    else:
        logger.info("[%d] Opening line prepared before dialing: %s", index, line)
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
    voicemail_path: Path,
    browser: GoogleVoiceBrowser,
    loopback_device: str,
    loopback_available: bool,
    call_max_duration: int,
    logger: logging.Logger,
    answered_speak_delay: float = 4.0,
    wait_for_human_audio: bool = True,
    human_audio_timeout: float = 8.0,
    stt_retry_count: int = 2,
    vad_silence_frames: int = 12,
    vad_speech_frames: int = 2,
    voicemail_detect_seconds: float = 15.0,
    tts_warmup: bool = True,
    silence_does_not_end_call: bool = True,
    use_stt_context: bool = True,
    max_silence_seconds: float = 8.0,
    llm_model_realtime: str = "llama-3.1-8b-instant",
    listen_after_tts_delay_ms: int = 150,
    use_thinking_fillers: bool = True,
    filler_probability: float = 0.7,
    stream_llm_replies: bool = True,
    voicemail_play_on_greeting: bool = True,
    voicemail_greeting_frames_required: int = 6,
    groq_max_retries_per_minute: int = 60,
    voicemail_max_wait_seconds: float = 8.0,
    voicemail_play_after_seconds: float = 4.0,
    shared_tts: object | None = None,
    tts_allow_sapi_fallback: bool = False,
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
        if shared_tts is not None:
            tts = shared_tts
        else:
            tts = RealtimeTTS(
                device_index=loopback_device_index,
                voice=tts_voice,
                use_cache=tts_warmup,
                allow_sapi_fallback=tts_allow_sapi_fallback,
            )
            if opening_line and hasattr(tts, "prewarm_line"):
                tts.prewarm_line(opening_line)  # type: ignore[attr-defined]
        agent = ConversationAgent(
            api_key=groq_api_key,
            model=llm_model_realtime,
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context,
            company_website=company_website,
            callback_number=callback_number,
            contact_name=contact_name,
            contact_phone=session.phone,
            rate_limit_per_minute=groq_max_retries_per_minute,
        )
        stt = GroqWhisperSTT(
            api_key=groq_api_key,
            model=stt_model,
            retry_count=stt_retry_count,
            use_stt_context=use_stt_context,
        )
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
            voicemail_detect_seconds=voicemail_detect_seconds,
            silence_does_not_end_call=silence_does_not_end_call,
            max_silence_seconds=max_silence_seconds,
            listen_after_tts_delay_ms=listen_after_tts_delay_ms,
            use_thinking_fillers=use_thinking_fillers,
            filler_probability=filler_probability,
            stream_llm_replies=stream_llm_replies,
            voicemail_play_on_greeting=voicemail_play_on_greeting,
            voicemail_greeting_frames_required=voicemail_greeting_frames_required,
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
        time.sleep(0.2)
        loop.run(session=session, opening_line=opening_line, auto_opening=True)
    except Exception as exc:
        logger.error("Realtime loop error: %s", exc)
        if not session.is_terminal():
            session.transition(CallState.FAILED, f"realtime loop error: {exc}")
    finally:
        monitor_stop.set()
        monitor.join(timeout=2.0)
        if session.state == CallState.VOICEMAIL:
            logger.info("Realtime voicemail detected; fast delivery: %s", voicemail_path)
            _deliver_voicemail_and_hangup(
                browser,
                session,
                voicemail_path,
                loopback_device,
                loopback_available,
                logger,
                max_wait=voicemail_max_wait_seconds,
                ready_after=voicemail_play_after_seconds,
            )
            return
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
    *,
    inactive_polls_required: int = 6,
    warmup_seconds: float = 5.0,
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

        if getattr(loop, "is_agent_busy", lambda: False)():
            inactive_hits = 0
        elif browser.is_call_active():
            inactive_hits = 0
        else:
            inactive_hits += 1
            if (
                time.monotonic() - started > warmup_seconds
                and inactive_hits >= inactive_polls_required
            ):
                logger.info(
                    "Google Voice call no longer active (%d polls); stopping realtime loop.",
                    inactive_hits,
                )
                if not session.is_terminal():
                    try:
                        session.transition(CallState.ENDED, "Google Voice call ended")
                    except ValueError:
                        pass
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
    logger.error(
        "No loopback device — cannot inject audio. Configure VB-CABLE / LOOPBACK_DEVICE. File: %s",
        path,
    )
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
    capture_device  = safe_capture_hint(args.capture_device or cfg.capture_device, loopback_device)
    tts_voice       = args.tts_voice or cfg.tts_voice

    if not callback_number:
        raise SystemExit(
            "CALLBACK_NUMBER is not configured. Add it to .env or pass --callback-number."
        )

    ai = GroqAgent(api_key=cfg.groq_api_key, model=cfg.llm_model_batch)
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
            min_ring_seconds=getattr(cfg, "min_ring_seconds", 0.0),
            max_ring_seconds=getattr(cfg, "max_ring_seconds", 45.0),
            voicemail_detect_seconds=getattr(cfg, "voicemail_detect_seconds", 15.0),
            tts_warmup=cfg.tts_warmup,
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
    from src.groq_pool import get_groq_pool

    try:
        pool = get_groq_pool(refresh=True)
        logger.info(
            "Groq API pool ready: %d key(s), failover enabled",
            pool.key_count,
        )
    except ValueError:
        pass
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
    capture_device = safe_capture_hint(args.capture_device or cfg.capture_device, loopback_device)
    tts_voice = args.tts_voice or cfg.tts_voice

    if not callback_number:
        raise SystemExit(
            "CALLBACK_NUMBER is not configured. Add it to .env or pass --callback-number."
        )

    from src.batch_progress import (
        contacts_fingerprint,
        filter_contacts,
        mark_completed,
        reset_progress,
    )

    logger.info("Loading contacts from %s", contact_path)
    all_contacts = load_contacts(contact_path)
    if not all_contacts:
        raise SystemExit("No valid contacts loaded — check your contacts file.")

    fingerprint = contacts_fingerprint(contact_path)
    if args.reset_batch_progress:
        reset_progress(fingerprint)
        logger.info("Cleared batch progress for %s", contact_path)

    contacts = filter_contacts(
        all_contacts[: args.limit],
        fingerprint,
        resume=not args.no_resume,
    )
    if cfg.use_call_intelligence:
        from src.call_intelligence import filter_dialable_contacts

        contacts, intel_skipped, _skipped_rows = filter_dialable_contacts(contacts)
        if intel_skipped:
            logger.info(
                "Call intelligence skipped %d contact(s) (DNC, wrong number, repeat failures)",
                intel_skipped,
            )
    if not contacts:
        raise SystemExit(
            "No contacts left to dial — all numbers in this list were completed. "
            "Use --no-resume or --reset-batch-progress to dial again."
        )
    logger.info(
        "Loaded %d contacts (%d in batch after resume filter), limit=%d",
        len(all_contacts),
        len(contacts),
        args.limit,
    )

    ai = GroqAgent(api_key=cfg.groq_api_key, model=cfg.llm_model_batch)

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
    if not args.dry_run and not loopback_available:
        raise SystemExit(
            "Live calling needs an output loopback device so TTS reaches Google Voice. "
            "Install/configure VB-CABLE, set LOOPBACK_DEVICE or --loopback-device to the "
            "playback cable input."
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
                min_ring_seconds=getattr(cfg, "min_ring_seconds", 0.0),
                max_ring_seconds=getattr(cfg, "max_ring_seconds", 45.0),
                voicemail_detect_seconds=getattr(cfg, "voicemail_detect_seconds", 15.0),
                tts_warmup=cfg.tts_warmup,
                silence_does_not_end_call=cfg.silence_does_not_end_call,
                use_stt_context=cfg.use_stt_context,
                max_silence_seconds=cfg.max_silence_seconds,
            )
        logger.info("Dry run complete. Audio files in %s/", output_dir)
        return

    browser_holder: list = []
    _install_shutdown_handlers(browser_holder)
    browser = GoogleVoiceBrowser(
        profile_name=profile_name,
        headless=args.headless,
        avoid_page_reload=cfg.avoid_gv_page_reload,
    )
    browser_holder.append(browser)
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

    logger.info("Logged in. Starting call loop (%d contacts).", len(contacts))

    if cfg.tts_warmup and loopback_device_index is not None:
        try:
            from src.opening_pool import warmup_phrases
            from src.tts_cache import TTSCache

            cache = TTSCache(tts_voice=tts_voice)
            cache.warm(warmup_phrases(agent_name, company_name))
        except Exception as exc:
            logger.debug("Opening TTS warmup skipped: %s", exc)

    call_cooldown_seconds = float(cfg.call_cooldown_seconds)
    from src.log_rotation import rotate_logs_if_needed

    shared_call_kwargs = dict(
        objective=args.objective,
        offer=args.offer,
        callback_number=callback_number,
        agent_name=agent_name,
        company_name=company_name,
        company_context=company_context,
        company_website=company_website,
        loopback_device=loopback_device,
        loopback_device_index=loopback_device_index,
        loopback_available=loopback_available,
        call_timeout=call_timeout,
        call_max_duration=call_max_duration,
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
        min_ring_seconds=cfg.min_ring_seconds,
        max_ring_seconds=cfg.max_ring_seconds,
        voicemail_detect_seconds=cfg.voicemail_detect_seconds,
        tts_warmup=cfg.tts_warmup,
        tts_allow_sapi_fallback=cfg.tts_allow_sapi_fallback,
        silence_does_not_end_call=cfg.silence_does_not_end_call,
        use_stt_context=cfg.use_stt_context,
        max_silence_seconds=cfg.max_silence_seconds,
        llm_model_realtime=cfg.llm_model_realtime,
        listen_after_tts_delay_ms=cfg.listen_after_tts_delay_ms,
        use_thinking_fillers=cfg.use_thinking_fillers,
        filler_probability=cfg.filler_probability,
        stream_llm_replies=cfg.stream_llm_replies,
        voicemail_max_wait_seconds=cfg.voicemail_max_wait_seconds,
        voicemail_play_after_seconds=cfg.voicemail_play_after_seconds,
        voicemail_play_on_greeting=cfg.voicemail_play_on_greeting,
        voicemail_greeting_frames_required=cfg.voicemail_greeting_frames_required,
        screening_purpose_text=cfg.screening_purpose_text,
        groq_max_retries_per_minute=_scaled_groq_rate_limit(cfg),
    )

    calls_completed = 0
    try:
        for i, contact in enumerate(contacts, start=1):
            if cfg.max_calls_per_run > 0 and calls_completed >= cfg.max_calls_per_run:
                logger.info("Reached max_calls_per_run=%d; stopping batch.", cfg.max_calls_per_run)
                break
            if i % 50 == 1:
                rotate_logs_if_needed()
            if not browser.is_logged_in():
                raise SystemExit("Google Voice login required.")
            completed_phone: str | None = None
            for attempt in range(2):
                try:
                    completed_phone = _run_call(
                        contact,
                        i,
                        browser,
                        ai,
                        call_logger,
                        logger,
                        script_dir,
                        voicemail_dir,
                        **shared_call_kwargs,
                    )
                    break
                except WebDriverException as exc:
                    logger.error(
                        "[%d] Chrome/WebDriver error (attempt %d/2): %s",
                        i,
                        attempt + 1,
                        exc,
                    )
                    if attempt == 0 and browser.recover_session():
                        logger.info("[%d] Browser recovered — retrying contact", i)
                        continue
                    logger.error("Stopping call loop — could not recover browser session.")
                    completed_phone = None
                    break
            if completed_phone:
                mark_completed(fingerprint, completed_phone, i)
                calls_completed += 1
            if (
                cfg.chrome_restart_every_n_calls > 0
                and calls_completed > 0
                and calls_completed % cfg.chrome_restart_every_n_calls == 0
            ):
                logger.info("Chrome restart interval reached (%d calls); refreshing session", calls_completed)
                browser.close()
                browser.launch()
                if not browser.is_logged_in():
                    logger.warning("Re-login required after Chrome restart")
                    if not browser.wait_for_manual_login(timeout=120):
                        break
            if browser.driver is None:
                logger.error("Chrome/Google Voice session is no longer available; stopping call loop.")
                break
            if call_cooldown_seconds > 0:
                time.sleep(call_cooldown_seconds)
            else:
                time.sleep(1.0)

    finally:
        browser_holder.clear()
        browser.close()

    logger.info("All calls complete. Logs: logs/call_logs.csv | Audio: %s/", output_dir)


if __name__ == "__main__":
    main()
