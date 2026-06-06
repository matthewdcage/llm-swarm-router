"""Discovery — local provider scan and swarm coordination."""

from netllm_discovery.local import KNOWN_PROVIDERS, scan_local_providers
from netllm_discovery.swarm import PeerRecord, SwarmRegistry

__all__ = [
    "KNOWN_PROVIDERS",
    "PeerRecord",
    "SwarmRegistry",
    "scan_local_providers",
]
