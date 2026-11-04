"""Two-hop end-to-end and forwarder isolation verification (BLOCKING 1 & BLOCKING 8).
Built from real server code (handle_client).
"""
import pytest
from services.instancer.tests.test_router_two_hop import (
    test_two_hop_end_to_end_forwarding,
    test_hop2_trusted_forwarder_preserves_per_player_limits,
    test_hop2_untrusted_peer_cannot_spoof_client_ip,
)
