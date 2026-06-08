"""Discovery — local provider scan and swarm coordination."""

from netllm_discovery.local import (
    KNOWN_PROVIDERS,
    candidate_urls_for_provider,
    merge_discovered_provider_urls,
    scan_local_providers,
)
from netllm_discovery.swarm import PeerRecord, SwarmRegistry

__all__ = [
    "KNOWN_PROVIDERS",
    "PeerRecord",
    "SwarmRegistry",
    "candidate_urls_for_provider",
    "merge_discovered_provider_urls",
    "scan_local_providers",
]
