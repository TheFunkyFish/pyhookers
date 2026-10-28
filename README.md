# pyhookers
Collection of hooking scripts mostly fastAPI and uvicorn

<<<<<<< HEAD
# Use Open Code with your remote servers. The most simplistitic of a proxy to llama.cpp or probably most inference engines that could be exposed. Use this to lock down the LLM to local host and this to your network or credentials, or BOTH!!!!

Whatever port, whatever host, reload if making changes to your implementation.
Dont forgeet your firewall opening.

python3 -m uvicorn open_code_hooker:app --host 0.0.0.0 --port 8082 --reload

** Use at your own risk. There is not authorization built into this as of October 28th,2026. Build your own simple routine that will allow open code to authenticate when it makes its requests/
=======
## open_code_hooker.py — UUID credential authentication

`open_code_hooker.py` now requires a **UUID credential** on every proxied request.
Credentials are stored in an **AES-256-GCM encrypted file**; only the holder of the
master key can decrypt it and check whether a presented UUID exists. Plaintext
credentials never touch disk.

### Files (created next to where you run, or via env vars)

| File | Purpose | Env override |
|---|---|---|
| `credentials.enc` | Encrypted list of valid UUID credentials (`OC1 ‖ nonce(12B) ‖ ciphertext+tag`) | `OPENCODE_CRED_FILE` |
| `key.key` | 32-byte AES master key, mode `0600` | — |
| *(env)* | Master key as hex/base64; wins over the key file when set | `OPENCODE_CRED_KEY` |

Set `AUTH_ENABLED=0` to disable authentication (local dev only).

### Setup

```bash
cd pyhookers
PY=/Library/lab/fastapi/bin/python   # venv with fastapi + cryptography installed

# Create key + credential store, seeded with one UUID:
$PY credential_manager.py init --seed "$(uuidgen | tr 'A-Z' 'a-z')"

# Manage later:
$PY credential_manager.py add <UUID>      # append a credential
$PY credential_manager.py remove <UUID>   # revoke a credential
$PY credential_manager.py list            # decrypt & print stored credentials
```

The `init` output prints the master key in hex — keep it (or the `key.key` file) secret.

### Making requests

Send the UUID as a Bearer token (works out of the box for OpenAI-compatible clients,
e.g. set it as `apiKey` in your provider config):

```bash
curl http://127.0.0.1:8082/xxx/v1/chat/completions \
  -H "Authorization: Bearer <uuid>" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}]}'
```

An explicit header also works on any proxied route: `-H "X-OpenCode-Credential: <uuid>"`.

Responses when auth fails:

| Status | Meaning |
|---|---|
| `401` | Missing credential, or not a valid UUID format |
| `403` | Valid UUID but not present in the encrypted store |
| `500` | Store missing/corrupt or wrong master key (fails closed) |

### Security notes

* **At rest:** only ciphertext + nonce + GCM tag; no plaintext UUIDs.
* **Key handling:** 256-bit random key from `secrets`; file created with mode `0600`.
* **Verification:** constant-time comparison (`hmac.compare_digest`) over every stored entry — no early-exit timing leak on which position matched.
* **Fail-closed:** a bad/missing key makes decryption raise, so requests get `4xx/5xx`, never an open proxy.
* `/api/info` and `/api/event` (OpenCode handshake/telemetry) remain unauthenticated by design.
>>>>>>> refs/remotes/origin/main
