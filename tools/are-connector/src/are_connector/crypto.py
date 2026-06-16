from __future__ import annotations

import base64
import shutil
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa, x25519, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography import x509
from nacl import bindings


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    padding_len = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + ("=" * padding_len))


@dataclass
class EphemeralKey:
    private_key: x25519.X25519PrivateKey
    public_key_b64: str
    attestation_type: str
    quote: str | None = None


def generate_ephemeral_key() -> EphemeralKey:
    private_key = x25519.X25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    attestation_type = "tpm2" if shutil.which("tpm2_getcap") else "software"
    return EphemeralKey(
        private_key=private_key,
        public_key_b64=b64url_encode(public_bytes),
        attestation_type=attestation_type,
    )


def decrypt_task_content(
    private_key: x25519.X25519PrivateKey,
    sender_public_key_b64: str,
    nonce_b64: str,
    ciphertext_b64: str,
) -> bytes:
    sender_public = x25519.X25519PublicKey.from_public_bytes(b64url_decode(sender_public_key_b64))
    shared = private_key.exchange(sender_public)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"are-connector-task-v0.1",
    ).derive(shared)
    return bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
        b64url_decode(ciphertext_b64),
        None,
        b64url_decode(nonce_b64),
        key,
    )


@dataclass
class EnvelopeSigner:
    private_key: ed25519.Ed25519PrivateKey | rsa.RSAPrivateKey
    alg: str

    @classmethod
    def generate(cls) -> "EnvelopeSigner":
        return cls(private_key=ed25519.Ed25519PrivateKey.generate(), alg="ed25519")

    def sign_b64(self, payload: bytes) -> str:
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            return b64url_encode(self.private_key.sign(payload))
        signature = self.private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        return b64url_encode(signature)

    def public_key_pem(self) -> str:
        public_key = self.private_key.public_key()
        return public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def private_key_pem(self) -> str:
        return self.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")

    @classmethod
    def from_private_key_pem(cls, pem: str) -> "EnvelopeSigner":
        private_key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
        if isinstance(private_key, ed25519.Ed25519PrivateKey):
            return cls(private_key=private_key, alg="ed25519")
        if isinstance(private_key, rsa.RSAPrivateKey):
            return cls(private_key=private_key, alg="rsa-2048")
        raise ValueError("unsupported envelope signing key type")


def verify_policy_signature(
    ca_chain_pem: str,
    policy_version: str,
    policy_body: bytes,
    signature_b64: str,
    counter_public_key_b64: str | None = None,
) -> None:
    signature = b64url_decode(signature_b64)
    if counter_public_key_b64:
        try:
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(b64url_decode(counter_public_key_b64))
            public_key.verify(signature, policy_body)
            return
        except InvalidSignature as exc:
            raise ValueError("redaction policy signature verification failed") from exc

    certs = [
        x509.load_pem_x509_certificate(block + b"-----END CERTIFICATE-----\n")
        for block in ca_chain_pem.encode("utf-8").split(b"-----END CERTIFICATE-----")
        if b"-----BEGIN CERTIFICATE-----" in block
    ]
    if not certs:
        raise ValueError("redaction policy cannot be verified without a CA certificate")

    public_key = certs[0].public_key()
    payloads = (policy_version.encode("utf-8") + b"." + policy_body, policy_body)

    try:
        for payload in payloads:
            try:
                if isinstance(public_key, ed25519.Ed25519PublicKey):
                    public_key.verify(signature, payload)
                elif isinstance(public_key, rsa.RSAPublicKey):
                    public_key.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
                else:
                    public_key.verify(signature, payload)
                return
            except InvalidSignature:
                continue
        raise InvalidSignature
    except InvalidSignature as exc:
        raise ValueError("redaction policy signature verification failed") from exc
