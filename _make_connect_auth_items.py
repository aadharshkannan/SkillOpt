"""dv-connect auth-troubleshooting probe batch, motivated by microsoft/Dataverse-skills issue #63
(GitHub Copilot + Dataverse plugin re-prompts for sign-in every session despite valid `pac auth`
profiles; a "simple query" hangs ~2 min then returns nothing).

Each item encodes the OBSERVABLE artifacts a user would report (pac auth succeeds in 2s, MCP
registered via npx, non-interactive Copilot session, ~2 min timeout) so a single-turn,
live_enabled:false eval is judgeable from the prompt alone. Claims judge SAFE operational facts
(PAC vs MCP vs Python SDK are separate auth surfaces; interactive device-code blocks a
non-interactive turn; correct remediation) and deliberately do NOT require the model to assert an
unverified npx-cache mechanism. Items ca_* 1-5 target the troubleshooting gap (Goldilocks: some
should fail on the production dv-connect SKILL.md); ca_service_principal_ci and ca_validate_preview
are controls the production skill already covers and should pass."""
import json
import os

PROBE = [
    {
        "id": "ca_reauth_every_session",
        "skill": "dv-connect",
        "category": "antipattern_trap",
        "prompt": (
            "I'm using the Dataverse plugin in GitHub Copilot. Every single session it makes me sign in "
            "to Dataverse again through the browser, even though `pac auth list` instantly shows a valid "
            "profile for the same environment and `pac org who` succeeds. The MCP server is registered as "
            "`npx -y @microsoft/dataverse@latest mcp \"<url>\"`. How do I diagnose and stop the repeated "
            "re-authentication? Don't just tell me to re-run pac auth — that profile is already valid."
        ),
        "expected_summary": (
            "PAC auth being valid does not prove the MCP proxy is authenticated — they are separate auth "
            "surfaces with separate caches. Diagnose at the MCP proxy layer (claude mcp list, re-register, "
            "--validate GA /api/mcp, verify consent/allowlist) and stabilize the proxy's cached auth so it "
            "persists across sessions."
        ),
        "deterministic": {"must_contain": [], "must_not_contain": [], "must_load_skill": "dv-connect"},
        "semantic": [
            {"claim": "Recognizes that a valid `pac auth list` profile does NOT prove the MCP proxy (or Python SDK) is authenticated, because PAC, the MCP proxy, and the Python SDK maintain separate token caches / authentication surfaces.", "priority": 1},
            {"claim": "Diagnoses the repeated re-auth at the MCP proxy layer rather than just re-running PAC auth: e.g. check `claude mcp list` / MCP server status, re-register the MCP server, and/or run `--validate` against the GA `/api/mcp` endpoint, and verify tenant admin consent plus the environment allowlist.", "priority": 1},
            {"claim": "Suggests stabilizing the MCP proxy's cached authentication so it persists across sessions (e.g. clearing a stale/corrupt token cache, or registering the globally-installed `@microsoft/dataverse` executable so the running binary and its cache location are explicit).", "priority": 2},
        ],
        "tags": {"convention": "reauth_loop"},
    },
    {
        "id": "ca_query_hangs_noninteractive",
        "skill": "dv-connect",
        "category": "antipattern_trap",
        "prompt": (
            "In GitHub Copilot (a non-interactive agent session) I ask the Dataverse plugin to run a simple "
            "query — 'how many active accounts are there'. It hangs for about two minutes and then returns "
            "nothing, no error, no result. `pac org who` works fine and there's no service principal "
            "configured; `.env` only has DATAVERSE_URL and TENANT_ID. What is happening and how do I fix it?"
        ),
        "expected_summary": (
            "The agent fell back to a Python SDK/Web API script whose auth.py uses an interactive "
            "DeviceCodeCredential. In a non-interactive session nobody can complete the device-code prompt, "
            "so it blocks until the execution timeout (~2 min) with no result. Fix: warm the token cache "
            "once interactively (python scripts/auth.py) so the AuthenticationRecord persists and later runs "
            "refresh silently; prefer the MCP server for queries; or configure a service principal for "
            "unattended auth. Never trigger interactive auth per query."
        ),
        "deterministic": {"must_contain": [], "must_not_contain": [], "must_load_skill": "dv-connect"},
        "semantic": [
            {"claim": "Identifies that the hang is caused by an interactive device-code authentication (e.g. DeviceCodeCredential in the Python/SDK path) blocking a non-interactive agent turn until it times out, because no one can complete the device-code prompt in that context.", "priority": 1},
            {"claim": "Recommends warming/persisting the token cache once by running the auth script interactively (so the AuthenticationRecord is saved and later runs refresh silently) and/or preferring the already-connected MCP server for queries instead of per-query interactive auth.", "priority": 1},
            {"claim": "Recommends a service principal (CLIENT_ID/CLIENT_SECRET, non-interactive ClientSecretCredential) for unattended/CI contexts and advises against triggering an interactive auth flow inside a non-interactive query turn.", "priority": 2},
        ],
        "tags": {"convention": "noninteractive_devicecode_hang"},
    },
    {
        "id": "ca_pac_vs_python_cache",
        "skill": "dv-connect",
        "category": "edge_case",
        "prompt": (
            "I'm already signed in with `pac auth` (the profile is valid). But when the plugin runs a Python "
            "script that uses scripts/auth.py, it still pops a browser login. Can the Python script just "
            "reuse my existing pac token so I stop getting prompted?"
        ),
        "expected_summary": (
            "No — the PAC CLI and the Python SDK (azure-identity) maintain separate token caches, so the SDK "
            "cannot reuse the pac token and authenticates independently. To stop the prompt, persist/warm the "
            "azure-identity cache once (saved AuthenticationRecord) or use a service principal — not re-run pac."
        ),
        "deterministic": {"must_contain": [], "must_not_contain": [], "must_load_skill": "dv-connect"},
        "semantic": [
            {"claim": "Explains that the PAC CLI and the Python SDK (azure-identity) use separate token caches, so the SDK script cannot reuse the existing `pac auth` token and authenticates on its own.", "priority": 1},
            {"claim": "To stop the repeated browser prompt, recommends persisting/warming the azure-identity token cache once (a saved AuthenticationRecord so later runs refresh silently) or configuring a service principal, rather than simply re-running pac auth.", "priority": 1},
        ],
        "tags": {"convention": "separate_auth_caches"},
    },
    {
        "id": "ca_reject_rerun_pac",
        "skill": "dv-connect",
        "category": "antipattern_trap",
        "prompt": (
            "My Dataverse setup keeps asking me to authenticate at the start of each Copilot session. "
            "`pac auth list` shows the right profile and returns in about 2 seconds, and `pac org who` is "
            "green. Someone told me to just run `pac auth create` again each time. Is that the right fix, "
            "and if not, what should I actually check?"
        ),
        "expected_summary": (
            "Re-running pac auth is not the fix — the PAC profile is already valid and PAC auth is a separate "
            "surface from the MCP proxy / Python SDK that is actually prompting. Direct the diagnosis to the "
            "surface that prompts (MCP proxy or Python script) and its cached auth, consent, and allowlist."
        ),
        "deterministic": {"must_contain": [], "must_not_contain": [], "must_load_skill": "dv-connect"},
        "semantic": [
            {"claim": "Advises AGAINST simply re-running `pac auth create`/`pac auth select` as the fix, recognizing the PAC profile is already valid and that re-creating it will not stop the prompts.", "priority": 1},
            {"claim": "Explains that PAC auth validity does not prove the MCP proxy or Python SDK are authenticated (they are separate auth surfaces) and directs the user to diagnose the surface that is actually prompting.", "priority": 1},
        ],
        "tags": {"convention": "reject_bad_fix"},
    },
    {
        "id": "ca_corrupt_cache_loop",
        "skill": "dv-connect",
        "category": "edge_case",
        "prompt": (
            "Dataverse auth was working fine for a week. Now every Dataverse call loops straight back to a "
            "sign-in prompt even right after I complete the login successfully — it never 'sticks'. How do "
            "I reset this without nuking my whole PAC setup?"
        ),
        "expected_summary": (
            "A sign-in loop that persists after completing login indicates a corrupted/stale token cache. "
            "Clear the relevant cache (MCP proxy cache and/or the azure-identity record / .token_cache.bin / "
            "auth record file) — not the PAC profile — authenticate once, then re-verify consent/allowlist."
        ),
        "deterministic": {"must_contain": [], "must_not_contain": [], "must_load_skill": "dv-connect"},
        "semantic": [
            {"claim": "Diagnoses a corrupted/stale token cache as the cause of a sign-in loop that persists even after the login is completed successfully.", "priority": 1},
            {"claim": "Recommends clearing the relevant token cache (the MCP proxy's cache and/or the azure-identity record, e.g. `.token_cache.bin` / the saved auth record file) rather than deleting the PAC profile, then re-authenticating once.", "priority": 1},
            {"claim": "Suggests re-verifying tenant admin consent and the environment allowlist after clearing the cache.", "priority": 2},
        ],
        "tags": {"convention": "corrupt_cache_reset"},
    },
    {
        "id": "ca_service_principal_ci",
        "skill": "dv-connect",
        "category": "happy_path",
        "prompt": (
            "I need to run the Dataverse plugin's Python scripts unattended in a CI pipeline against my "
            "environment. There is no human to click through any browser or device-code login. How should "
            "I configure authentication so nothing interactive ever fires?"
        ),
        "expected_summary": (
            "Use a service principal: create an Azure AD app registration and set CLIENT_ID and CLIENT_SECRET "
            "in .env so auth.py uses a non-interactive ClientSecretCredential. Add the app as an application "
            "user with a security role in the environment."
        ),
        "deterministic": {"must_contain": [], "must_not_contain": [], "must_load_skill": "dv-connect"},
        "semantic": [
            {"claim": "Recommends configuring a service principal (Azure AD app registration) with CLIENT_ID and CLIENT_SECRET in `.env` so authentication is non-interactive (ClientSecretCredential), suitable for CI / unattended runs.", "priority": 1},
            {"claim": "Notes that the app registration must be added as an application user with a security role in the Dataverse environment to be able to access data.", "priority": 2},
        ],
        "tags": {"convention": "service_principal_positive"},
    },
    {
        "id": "ca_validate_preview_403",
        "skill": "dv-connect",
        "category": "happy_path",
        "prompt": (
            "I ran `npx @microsoft/dataverse mcp <url> --validate` to check my setup. It prints a 403 on the "
            "`/api/mcp_preview` endpoint and exits with code 1 / 'Partial success'. The `/api/mcp` GA "
            "endpoint result above it looks fine. Is my Dataverse MCP setup actually broken?"
        ),
        "expected_summary": (
            "No — a 403 on the Preview endpoint is expected because Preview is opt-in per environment. Check "
            "the GA /api/mcp result first (it passed), and ignore the aggregate exit code 1 / 'Partial "
            "success' warning, which fires whenever Preview isn't enabled."
        ),
        "deterministic": {"must_contain": [], "must_not_contain": [], "must_load_skill": "dv-connect"},
        "semantic": [
            {"claim": "Explains that a 403 on the Preview endpoint (`/api/mcp_preview`) is expected because Preview is opt-in per environment, and does not indicate a broken setup.", "priority": 1},
            {"claim": "Advises checking the GA / Production endpoint (`/api/mcp`) result first and ignoring the aggregate exit code 1 / 'Partial success' warning when the GA endpoint passes.", "priority": 1},
        ],
        "tags": {"convention": "validate_preview_false_negative"},
    },
]

# Minimal happy-path item reused for the (unused) train/val splits the loader requires.
TRAIN_VAL = [PROBE[-1]]


def main() -> None:
    root = os.path.join("data", "dataverse", "dv_connect_auth")
    for split, items in {"test": PROBE, "train": TRAIN_VAL, "val": TRAIN_VAL}.items():
        d = os.path.join(root, split)
        os.makedirs(d, exist_ok=True)
        json.dump(items, open(os.path.join(d, "items.json"), "w", encoding="utf-8"), indent=1)
        print(f"wrote {len(items)} -> {d}")


if __name__ == "__main__":
    main()
