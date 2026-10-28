"""
Credential Manager for open_code_hooker.py
==========================================

Stores a list of UUID credentials in an AES-GCM encrypted file so that the
plaintext credential list is never visible on disk without the key.

Security model
--------------
* The credential store (default: ./credentials.enc) contains ONLY ciphertext,
  a random nonce and an HMAC tag -- no plaintext UUIDs at rest.
* A 256-bit master key decrypts it. The key lives either in a separate key
  file (default ./key.key, mode 0600) or in the OPENCODE_CRED_KEY environment
  variable (hex or base64). If both exist, the env var wins -- useful for CI
  where you don't want to ship the key file around.
* Verification uses constant-time comparison of candidate UUIDs against the
  decrypted list to avoid timing side-channels on which credential matched.

File formats
------------
credentials.enc : b"OC1" || nonce(12B) || ciphertext+tag   (AES-256-GCM over JSON {"uuid": [...]})
key.key         : 32 random bytes (raw), created with mode 0600

Usage from open_code_hooker.py:
    from credential_manager import CredentialManager, CREDENTIAL_FILE, KEY_FILE
    creds = CredentialManager(CREDENTIAL_FILE, KEY_FILE).load()
"""

import base64
import hmac
import json
import os
import secrets
import uuid as _uuid_mod

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

STORE_MAGIC = b"OC1"          # version tag for the encrypted store
NONCE_LEN = 12                # standard GCM nonce size

CREDENTIAL_FILE_DEFAULT = "credentials.enc"
KEY_FILE_DEFAULT = "key.key"


def is_valid_uuid(value) -> bool:
    """Return True if value parses as a canonical UUID string."""
    try:
        _uuid_mod.UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False


def constant_time_contains(stored: list[str], candidate: str) -> bool:
    """Check membership with per-entry constant-time comparison.

    Every stored entry is compared regardless of where the match occurs, so an
    attacker cannot infer which position (or whether any) matched from timing.
    """
    target = candidate.encode("utf-8")
    found = False
    for entry in stored:
        if hmac.compare_digest(entry.encode("utf-8"), target):
            found = True
    return found


class CredentialManager:
    def __init__(self, credential_file=CREDENTIAL_FILE_DEFAULT, key_file=KEY_FILE_DEFAULT):
        self.credential_file = os.path.abspath(credential_file)
        self.key_file = os.path.abspath(key_file)

    # ------------------------------------------------------------------ keys
    @staticmethod
    def generate_key() -> bytes:
        return AESGCM.generate_key(bit_length=256)  # 32 random bytes

    def save_key(self, key: bytes | None = None) -> str:
        """Persist a fresh (or given) master key to the key file with mode 0600."""
        if key is None:
            key = self.generate_key()
        fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return self.key_file

    def load_key(self) -> bytes:
        """Load the master key. Env var OPENCODE_CRED_KEY (hex or base64) wins;
        otherwise read from the key file."""
        env = os.environ.get("OPENCODE_CRED_KEY")
        if env:
            token = env.strip()
            for name, decoder in (("hex", lambda s: bytes.fromhex(s)), ("base64", base64.b64decode)):
                try:
                    raw = decoder(token)
                    break
                except Exception:
                    continue
            else:
                raise ValueError(
                    "OPENCODE_CRED_KEY must be hex or base64 encoded 32-byte key"
                )
            if len(raw) != 32:
                raise ValueError(f"{name} OPENCODE_CRED_KEY decoded to {len(raw)} bytes; expected 32")
            return raw

        with open(self.key_file, "rb") as fh:
            key = fh.read()
        if len(key) != 32:
            raise ValueError(f"{self.key_file} must contain a 32-byte AES-256 key")
        return key

    # ------------------------------------------------------------- store I/O
    def _encrypt_store(self, uuids: list[str], key: bytes) -> bytes:
        plaintext = json.dumps({"uuid": sorted(set(uuids))}, sort_keys=True).encode("utf-8")
        nonce = secrets.token_bytes(NONCE_LEN)
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        return STORE_MAGIC + nonce + ct

    def _decrypt_store(self, blob: bytes, key: bytes) -> list[str]:
        if not blob.startswith(STORE_MAGIC):
            raise ValueError("Unrecognized credential file format (missing OC1 magic)")
        body = blob[len(STORE_MAGIC):]
        if len(body) < NONCE_LEN + 16:
            raise ValueError("Credential file too short to be valid")
        nonce, ciphertext = body[:NONCE_LEN], body[NONCE_LEN:]
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise PermissionError(
                "Could not decrypt credentials -- wrong key or corrupted store"
            ) from exc
        data = json.loads(plaintext.decode("utf-8"))
        return [u for u in data.get("uuid", []) if is_valid_uuid(u)]

    # -------------------------------------------------------------- public API
    def create(self, uuids: list[str] | None = None, force_new_key=False) -> dict:
        """Create the credential file (and optionally a fresh master key)."""
        if force_new_key or not os.path.exists(self.key_file):
            self.save_key()
        key = self.load_key()
        blob = self._encrypt_store(uuids or [], key)
        fd = os.open(self.credential_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
        return {"credential_file": self.credential_file, "key_file": self.key_file}

    def load(self) -> list[str]:
        """Decrypt and return the credential UUID list (empty if none stored)."""
        with open(self.credential_file, "rb") as fh:
            blob = fh.read()
        return self._decrypt_store(blob, self.load_key())

    def add_credential(self, new_uuid: str) -> None:
        if not is_valid_uuid(new_uuid):
            raise ValueError(f"{new_uuid!r} is not a valid UUID")
        existing = set(self.load())
        existing.add(str(_uuid_mod.UUID(str(new_uuid))))  # canonical form
        key = self.load_key()
        fd = os.open(self.credential_file, os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, self._encrypt_store(sorted(existing), key))
        finally:
            os.close(fd)

    def remove_credential(self, target_uuid: str) -> None:
        if not is_valid_uuid(target_uuid):
            raise ValueError(f"{target_uuid!r} is not a valid UUID")
        target = str(_uuid_mod.UUID(str(target_uuid)))
        remaining = [u for u in self.load() if u != target]
        key = self.load_key()
        fd = os.open(self.credential_file, os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, self._encrypt_store(remaining, key))
        finally:
            os.close(fd)

    def verify_credential(self, candidate) -> bool:
        """Constant-time check that `candidate` exists among stored credentials."""
        if not is_valid_uuid(candidate):
            return False
        return constant_time_contains(self.load(), str(_uuid_mod.UUID(str(candidate))))

    # ------------------------------------------------------------- CLI helpers
    @staticmethod
    def _print_key_hint(key_file: str) -> None:
        try:
            with open(os.path.abspath(key_file), "rb") as fh:
                print(f"  key file : {os.path.abspath(key_file)} (hex: {fh.read().hex()})")
        except OSError:
            pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Manage the encrypted OpenCode credential store.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create key + empty (or seeded) credential file")
    p_init.add_argument("--seed", action="append", default=[], metavar="UUID",
                        help="Seed a UUID into the new store (repeatable)")
    p_add = sub.add_parser("add", help="Add a UUID to the existing store")
    p_add.add_argument("uuid")
    p_rm = sub.add_parser("remove", help="Remove a UUID from the store")
    p_rm.add_argument("uuid")
    p_ls = sub.add_parser("list", help="Decrypt and print stored credentials")

    args = parser.parse_args()
    mgr = CredentialManager()

    if args.cmd == "init":
        info = mgr.create(uuids=args.seed)
        print(f"Created credential file: {info['credential_file']}")
        CredentialManager._print_key_hint(info["key_file"])
    elif args.cmd == "add":
        mgr.add_credential(args.uuid)
        print(f"Added {args.uuid}")
    elif args.cmd == "remove":
        mgr.remove_credential(args.uuid)
        print(f"Removed {args.uuid}")
    else:
        for u in mgr.load():
            print(u)
