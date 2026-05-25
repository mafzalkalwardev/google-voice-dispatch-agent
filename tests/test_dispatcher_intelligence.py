from src.dispatcher_intelligence import (
    DispatcherConversationState,
    build_dynamic_system_prompt,
    build_guardrail_reply,
    build_pricing_reply,
    default_percentage_for,
    negotiation_floor_for,
    update_state_from_utterance,
)


def test_pricing_defaults_by_equipment():
    assert default_percentage_for("Box Truck") == 10
    assert default_percentage_for("Sprinter Van") == 15
    assert default_percentage_for("Dry Van") == 6
    assert default_percentage_for("Power Only") == 8


def test_negotiation_floor_only_moves_one_or_two_points():
    assert negotiation_floor_for("Sprinter Van") == 13
    assert negotiation_floor_for("Box Truck") == 8
    assert negotiation_floor_for("Dry Van") == 5


def test_state_tracks_trucking_details():
    state = DispatcherConversationState()
    update_state_from_utterance(
        state,
        "We run a 53ft reefer, MC 123456, OTR Midwest to Texas, OTR Capital factoring, straps and load bars.",
    )
    assert state.truck_type == "Reefer"
    assert state.mc_number == "MC-123456"
    assert state.local_or_otr == "OTR"
    assert "Midwest" in state.preferred_lanes
    assert state.factoring_company == "Otr Capital"
    assert "straps" in state.accessories


def test_dynamic_prompt_contains_modular_knowledge_sections():
    state = DispatcherConversationState(truck_type="Flatbed", preferred_lanes="Texas")
    prompt = build_dynamic_system_prompt(
        agent_name="Tony",
        company_name="Indus Transports LLC",
        company_website="https://industransports.online/",
        callback_number="+15551234567",
        company_context="Dispatch support",
        contact_name="Sam",
        state=state,
    )
    for section in (
        "MARKET KNOWLEDGE",
        "EQUIPMENT KNOWLEDGE",
        "NEGOTIATION RULES",
        "COMPLIANCE RULES",
        "OBJECTION HANDLING",
        "LANE STRATEGY",
        "PAYMENT AND FACTORING KNOWLEDGE",
    ):
        assert section in prompt
    assert "Texas outbound is generally strong" in prompt
    assert "Flatbed" in prompt


def test_pricing_reply_asks_for_expected_percentage_first():
    state = DispatcherConversationState(truck_type="Hotshot")
    reply = build_pricing_reply(state, "What do you charge?")
    assert "10%" in reply
    assert "What percentage were you hoping for?" in reply


def test_pricing_reply_counters_too_low_without_desperation():
    state = DispatcherConversationState(truck_type="Sprinter Van")
    reply = build_pricing_reply(state, "Can you do 10%?")
    assert "cannot do 10%" in reply
    assert "13-15%" in reply
    assert state.negotiated_percentage == "13%"


def test_guardrail_blocks_guarantees():
    state = DispatcherConversationState()
    reply = build_guardrail_reply(state, "Can you guarantee me loads every week?")
    assert "cannot guarantee" in reply.lower()
    assert "market moves" in reply.lower()


def test_interruption_recovery_is_short():
    state = DispatcherConversationState()
    reply = build_guardrail_reply(state, "wait, you cut me off")
    assert reply == "You are right, go ahead. What were you saying?"
