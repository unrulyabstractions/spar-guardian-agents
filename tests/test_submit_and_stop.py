"""Offline tests of submit and stop_working: who holds which tool, the guard, the records, the halt and the system
text the provider receives. No Docker, no credentials, no paid calls."""
from __future__ import annotations

from orbit.agents.turn_react import DEFAULT_ASSISTANT_PROMPT_TURN_REACT as ORBIT_ASSISTANT_PROMPT

from src.swarm.turn_rules import provider_system_text


def test_provider_system_text_drops_orbits_paragraph():
    ours = "You are a01.\n\n- submit: hand in your work and end the run."
    # turn_react without a submit tool: our instructions, a blank line, then ORBIT's paragraph.
    assert provider_system_text(f"{ours}\n\n{ORBIT_ASSISTANT_PROMPT}") == ours
    assert provider_system_text(ORBIT_ASSISTANT_PROMPT) == ""
    assert provider_system_text(ours) == ours
    assert "helpful assistant" not in provider_system_text(f"{ours}\n\n{ORBIT_ASSISTANT_PROMPT}")
