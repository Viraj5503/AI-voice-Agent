"""Jamie's brain.

`prompts.build_jamie_system_prompt` is the single entry point.  It fuses the
mock CRM "Known Context" with the live ClaimState (which pillars are still
unfilled) into a system prompt that keeps Jamie focused on what she actually
needs to ask.
"""
