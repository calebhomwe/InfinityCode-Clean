"""Team Engine — the deterministic multi-agent runtime.

Core IP per docs/TEAM_ENGINE_BRIEF.md. This package deliberately contains no
LLM calls: orchestration is state-machine-driven, agents are stateless
containers invoked by the engine, and every handoff between agents passes
through the typed HandoffArtifact schema defined in `runtime.schema.handoff`.

Layout
------
    runtime/
      schema/         # Typed contracts everything else validates against
        handoff.py    #   HandoffArtifact v2.0 (J1)
        evidence.py   #   Evidence Dossier + Claim (J3)
      engine/         # State machine + orchestration primitives (J2)
        state_machine.py
      dom/            # Document Object Model (J5)
        nodes.py

If you are about to solve a coordination problem with an LLM in this
package, stop and build a state machine instead.
"""
