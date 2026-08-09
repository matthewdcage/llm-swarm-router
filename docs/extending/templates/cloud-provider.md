# Stub — a cloud provider

Guide: [../01-cloud-provider.md](../01-cloud-provider.md) ·
Kit: `tests/conformance/kit_cloud.py`

## 1. Registry entry

`packages/netllm-core/src/netllm_core/cloud_providers.py`, inside
`CLOUD_PROVIDERS`:

```python
    "myvendor": CloudProviderSpec(
        id="myvendor",
        display_name="My Vendor",
        endpoints={
            # FIRST KEY IS THE DEFAULT REGION.
            "global": CloudEndpoint(
                openai_base_url="https://api.myvendor.example/v1",
                anthropic_base_url="https://api.myvendor.example/anthropic",
            ),
        },
        auth_modes=("api_key",),               # api_key | oauth_pkce | plan_token
        api_key_env="MYVENDOR_API_KEY",        # MUST be <ID>_API_KEY
        default_api_format="openai",
        models_endpoint=True,                  # False ⟹ static_models required
        static_models=("myvendor-large",),
        notes="Anything an operator needs to know that the fields cannot say.",
        # keychain_account="",                 # leave empty: <id>_api_key
    ),
```

## 2. Companion 1 — `CloudProviderId` (static-only)

Same file, directly above the registry:

```python
CloudProviderId = Literal[
    "moonshot", "zai", "openai", "anthropic", "openrouter", "dashscope",
    "myvendor",
]
```

Nothing raises if you skip this. `kit_cloud`'s `get_args` assertion is the
only thing that notices.

## 3. Companion 2 — macOS Settings offline roster (projection-enforced)

`apps/netllm-mac/Sources/AppView/SettingsViewModel.swift`, inside
`cloudProvidersBootstrap`:

```swift
        CloudProviderInfo(
            id: "myvendor",
            displayName: "My Vendor",
            notes: "Anything an operator needs to know.",
            regions: ["global"],
            keychainAccount: KeychainStore.accountForCloudProvider("myvendor")
        ),
```

## 4. Companion 3 — example config stanza (projection-enforced)

`config.example.toml`, beside the others:

```toml
# [cloud.providers.myvendor]
# enabled = true
# region = "global"
# # api_key_env = "MYVENDOR_API_KEY"   # or set the env var directly
```

## 5. Regenerate

```bash
python3 scripts/generate-registry-artifacts.py
```

## 6. Verify

```bash
uv run pytest tests/conformance/kit_cloud.py -k myvendor
uv run pytest tests/extending -q
./scripts/ci.sh lint
```

## What is still on you

Nothing here proves the base URL is right, the auth mode is real, or the
model ids exist. There is no provider canary in this tree
([../01-cloud-provider.md](../01-cloud-provider.md), unguarded rows).
