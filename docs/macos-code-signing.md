# macOS code signing and notarization

Release DMGs are **Developer ID signed and notarized** when GitHub Actions secrets are configured. Without secrets, builds fall back to ad-hoc signing and users may see Gatekeeper prompts ([macos-troubleshooting.md#gatekeeper-blocks-install-or-launch](macos-troubleshooting.md#gatekeeper-blocks-install-or-launch)).

## One-time setup

### 1. Confirm your Developer ID certificate

On your Mac:

```bash
security find-identity -v -p codesigning | grep "Developer ID Application"
```

You should see something like `Developer ID Application: Your Name (TEAMID)`.

### 2. Export the certificate for CI

1. Open **Keychain Access**
2. Select **login** keychain → **My Certificates**
3. Expand **Developer ID Application: …**
4. Select the certificate **and** its private key
5. **File → Export Items…** → format **Personal Information Exchange (.p12)**
6. Choose a strong export password (this becomes `MACOS_CERTIFICATE_PASSWORD`)

Base64-encode for GitHub:

```bash
base64 -i ~/Downloads/DeveloperID.p12 | pbcopy
# paste into GitHub secret MACOS_CERTIFICATE_P12
```

### 3. Create an app-specific password

At [appleid.apple.com](https://appleid.apple.com) → **Sign-In and Security** → **App-Specific Passwords** → generate one for `notarytool` / GitHub Actions.

### 4. Add GitHub repository secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret | Value |
|--------|--------|
| `MACOS_CERTIFICATE_P12` | Full base64 output of your `.p12` (single line) |
| `MACOS_CERTIFICATE_PASSWORD` | Password you set when exporting the `.p12` |
| `KEYCHAIN_PASSWORD` | Any strong random string (ephemeral CI keychain only) |
| `APPLE_ID` | Apple ID email used for the developer account |
| `APPLE_TEAM_ID` | 10-character Team ID ([Membership details](https://developer.apple.com/account#MembershipDetailsCard)) |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password from step 3 |

All six secrets are required for notarized releases. If only the first three are set, the app is signed but the DMG is not notarized (Gatekeeper may still warn on first open).

### 5. Verify locally before the next tag

```bash
apps/netllm-mac/Scripts/build.sh release
bash packaging/scripts/import-codesign-cert.sh   # after exporting env vars locally
bash packaging/scripts/codesign-mac-app.sh apps/netllm-mac/build/Stage/llm-swarm-router.app
bash packaging/scripts/create-dmg.sh
bash packaging/scripts/notarize-dmg.sh dist/llm-swarm-router.dmg
```

Local import (paste base64 into a temp file or export from Keychain directly):

```bash
export MACOS_CERTIFICATE_P12="$(base64 -i ~/Downloads/DeveloperID.p12)"
export MACOS_CERTIFICATE_PASSWORD='your-p12-export-password'
export KEYCHAIN_PASSWORD='local-test-keychain'
export APPLE_ID='you@example.com'
export APPLE_TEAM_ID='XXXXXXXXXX'
export APPLE_APP_SPECIFIC_PASSWORD='xxxx-xxxx-xxxx-xxxx'
```

Verify Gatekeeper acceptance:

```bash
spctl -a -t exec -vv apps/netllm-mac/build/Stage/llm-swarm-router.app
hdiutil attach dist/llm-swarm-router.dmg
spctl -a -t open --context context:primary-signature -v /Volumes/llm-swarm-router/llm-swarm-router.app
```

## CI flow (release workflow)

`.github/workflows/release.yml` `build-macos` job:

1. Build Stage app (ad-hoc during `build.sh` if cert not imported yet)
2. Run menubar e2e + lifecycle tests
3. `import-codesign-cert.sh` — import `.p12` into ephemeral keychain
4. `codesign-mac-app.sh` — Developer ID + Hardened Runtime + entitlements (`packaging/macos/entitlements.plist`)
5. `create-dmg.sh`
6. `maybe-notarize-dmg.sh` — submit DMG to Apple, staple ticket
7. SHA256 sidecar (computed **after** notarization/staple)

## Entitlements

Embedded venvstacks Python requires relaxed hardened-runtime flags in `packaging/macos/entitlements.plist` (unsigned executable memory, library validation). Adjust only if notary returns specific entitlement errors.

## Related

- Build: [packaging/README.md](../packaging/README.md)
- CI/release: [ci-and-release.md](ci-and-release.md)
- User install: [macos-install.md](macos-install.md)
