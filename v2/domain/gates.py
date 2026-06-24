"""
domain/gates.py — the ``--mock`` log/command **vocabulary** for the demo fleet.

> **Gate *logic* no longer lives here.** Health gates are now operator-editable config
> under ``gates/`` (one YAML per gate), parsed + evaluated by the generic engine
> ``core/gates_config.py`` and run by ``core/orchestrator.py`` (config over code, P8 —
> see ``plan2.md``). The old hard-coded parsers + ``compute_gates`` + thresholds were
> removed in that refactor.

What remains is the small set of **strings** the ``--mock`` producer
(:mod:`domain.mock_rules`) uses to synthesize the demo's *log content* and to **route**
collector / probe / build commands to the right simulated output. They are the demo's
log vocabulary — the live-log panes on the node-detail page (``rx`` / ``serviceB`` /
``serviceC`` tabs) tail these strings under ``--mock`` via ``stream_kind`` /
``stream_line`` / ``domain_read``. They are *not* a gate parser contract anymore (the
mock's gate behavior is the kind-aware :func:`domain.mock_rules.gate_mock`, which keys off
the simulated world, not text).

``mock_rules`` imports these from here, so there is one source: the Compiler patches the
``contract:`` strings (a ``gate-*`` sub-part) and the producer follows. The brand tokens
``ccflet`` / ``/tmp/ccflet`` stay (CLAUDE.md §8).
"""

# The demo "good" check value the producer prints on a [CHECK] line.
CHECK_GOOD = 3

# --- log-content vocabulary (what the mock prints into the demo logs) --------
# These markers are what domain.mock_rules emits into the simulated log streams; the
# live-log panes tail them. A fork can rename them (Compiler patches the `contract:`
# block) and the producer follows automatically, since mock_rules imports from here.
CHECK_TAG = "[CHECK]"           # serviceB.log line carrying the path-1 check value
CHECK2_TAG = "[CHECK2]"         # the path-2 check value (variant B)
PROBE_A_READY = "PROBEA: READY" # probe A "ready" marker
PROBE_B_OK = "PROBEB_OK"        # probe B "ok" marker
CHECK_VALUE_KEY = "value"       # token before the numeric value on a CHECK line
SIGNAL_KEY = "signal"           # token before the serviceC signal value

# --- command-routing markers (which simulated read a synthesized command maps to) ---
# Substrings of the matching profile collector/probe/build commands, so a fork whose
# profiles tail different log files / hit different probe endpoints still routes correctly
# under --mock. mock_rules imports them from here (one source).
LINKS_CMD_MARK = "links.json"     # links/peers collector command marker
CHECK_LOG_MARK = "serviceB.log"   # check collector command marker
SERVICEC_LOG_MARK = "serviceC.log"  # serviceC stats collector command marker
PROBE_A_CMD_MARK = "probeA"       # probe A command marker
PROBE_B_CMD_MARK = "probeB"       # probe B command marker
BUILD_CMD_MARK = "serviceA"       # serviceA_build (compile) command marker
