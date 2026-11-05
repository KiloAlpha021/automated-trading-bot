"""Narrow CNG ECDSA boundary for canonical operational admissions only."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from hashlib import sha256

from automated_trading_bot.stage5.authority_service.protocol import (
    AdmissionEnvelope,
    AuthorityServiceError,
    SignedAdmission,
)


NCRYPT_SILENT_FLAG = 0x00000040
BCRYPT_ECCPUBLIC_BLOB = "ECCPUBLICBLOB"


def _status(value: int, operation: str) -> None:
    if value != 0:
        raise AuthorityServiceError(
            f"CNG {operation} failed: 0x{value & 0xFFFFFFFF:08x}"
        )


class _Cng:
    def __init__(self) -> None:
        self.ncrypt = ctypes.WinDLL("ncrypt.dll")
        self.bcrypt = ctypes.WinDLL("bcrypt.dll")
        handle_pointer = ctypes.POINTER(wintypes.HANDLE)
        byte_pointer = ctypes.POINTER(ctypes.c_ubyte)
        dword_pointer = ctypes.POINTER(wintypes.DWORD)
        self.ncrypt.NCryptOpenStorageProvider.argtypes = [
            handle_pointer,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        self.ncrypt.NCryptOpenStorageProvider.restype = wintypes.LONG
        self.ncrypt.NCryptOpenKey.argtypes = [
            wintypes.HANDLE,
            handle_pointer,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.ncrypt.NCryptOpenKey.restype = wintypes.LONG
        self.ncrypt.NCryptCreatePersistedKey.argtypes = [
            wintypes.HANDLE,
            handle_pointer,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.ncrypt.NCryptCreatePersistedKey.restype = wintypes.LONG
        self.ncrypt.NCryptFinalizeKey.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.ncrypt.NCryptFinalizeKey.restype = wintypes.LONG
        self.ncrypt.NCryptSignHash.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            byte_pointer,
            wintypes.DWORD,
            byte_pointer,
            wintypes.DWORD,
            dword_pointer,
            wintypes.DWORD,
        ]
        self.ncrypt.NCryptSignHash.restype = wintypes.LONG
        self.ncrypt.NCryptExportKey.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            byte_pointer,
            wintypes.DWORD,
            dword_pointer,
            wintypes.DWORD,
        ]
        self.ncrypt.NCryptExportKey.restype = wintypes.LONG
        self.ncrypt.NCryptFreeObject.argtypes = [wintypes.HANDLE]
        self.ncrypt.NCryptFreeObject.restype = wintypes.LONG
        self.bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
            handle_pointer,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        self.bcrypt.BCryptOpenAlgorithmProvider.restype = wintypes.LONG
        self.bcrypt.BCryptImportKeyPair.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            handle_pointer,
            byte_pointer,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.bcrypt.BCryptImportKeyPair.restype = wintypes.LONG
        self.bcrypt.BCryptVerifySignature.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            byte_pointer,
            wintypes.DWORD,
            byte_pointer,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.bcrypt.BCryptVerifySignature.restype = wintypes.LONG
        self.bcrypt.BCryptDestroyKey.argtypes = [wintypes.HANDLE]
        self.bcrypt.BCryptDestroyKey.restype = wintypes.LONG
        self.bcrypt.BCryptCloseAlgorithmProvider.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.bcrypt.BCryptCloseAlgorithmProvider.restype = wintypes.LONG


class CngAdmissionSigner:
    """Own one CNG key handle and sign only canonical AdmissionEnvelope values."""

    def __init__(
        self, *, key_handle: int, key_id: str, key_version: int, cng: _Cng | None = None
    ) -> None:
        if not key_id or key_version < 1 or not key_handle:
            raise AuthorityServiceError("invalid CNG signing-key identity")
        self._cng = cng or _Cng()
        self._handle = key_handle
        self._key_id = key_id
        self._key_version = key_version

    @classmethod
    def open_provisioned(
        cls,
        *,
        key_name: str,
        key_id: str,
        key_version: int,
        expected_public_blob: bytes,
    ) -> CngAdmissionSigner:
        cng = _Cng()
        provider = wintypes.HANDLE()
        key = wintypes.HANDLE()
        try:
            _status(
                cng.ncrypt.NCryptOpenStorageProvider(
                    ctypes.byref(provider),
                    "Microsoft Software Key Storage Provider",
                    0,
                ),
                "open provider",
            )
            _status(
                cng.ncrypt.NCryptOpenKey(
                    provider,
                    ctypes.byref(key),
                    key_name,
                    0,
                    NCRYPT_SILENT_FLAG,
                ),
                "open provisioned key",
            )
            if key.value is None:
                raise AuthorityServiceError("CNG returned no provisioned key handle")
            value = cls(
                key_handle=key.value,
                key_id=key_id,
                key_version=key_version,
                cng=cng,
            )
            key.value = None
            if value.public_blob() != expected_public_blob:
                value.close()
                raise AuthorityServiceError(
                    "provisioned CNG key does not match protected public identity"
                )
            return value
        finally:
            if key.value:
                cng.ncrypt.NCryptFreeObject(key)
            if provider.value:
                cng.ncrypt.NCryptFreeObject(provider)

    @classmethod
    def engineering_ephemeral(
        cls, *, key_id: str = "ENGINEERING_ONLY", key_version: int = 1
    ) -> CngAdmissionSigner:
        """Create a non-persisted test key; never establishes operational authority."""
        cng = _Cng()
        provider = wintypes.HANDLE()
        key = wintypes.HANDLE()
        try:
            _status(
                cng.ncrypt.NCryptOpenStorageProvider(
                    ctypes.byref(provider),
                    "Microsoft Software Key Storage Provider",
                    0,
                ),
                "open provider",
            )
            _status(
                cng.ncrypt.NCryptCreatePersistedKey(
                    provider,
                    ctypes.byref(key),
                    "ECDSA_P256",
                    None,
                    0,
                    0,
                ),
                "create ephemeral key",
            )
            _status(cng.ncrypt.NCryptFinalizeKey(key, 0), "finalize key")
            if key.value is None:
                raise AuthorityServiceError("CNG returned no ephemeral key handle")
            return cls(
                key_handle=key.value,
                key_id=key_id,
                key_version=key_version,
                cng=cng,
            )
        finally:
            if provider.value:
                cng.ncrypt.NCryptFreeObject(provider)

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def key_version(self) -> int:
        return self._key_version

    def sign_admission(self, envelope: AdmissionEnvelope) -> bytes:
        if type(envelope) is not AdmissionEnvelope:
            raise AuthorityServiceError("CNG signs canonical admissions only")
        if not self._handle:
            raise AuthorityServiceError("CNG signing key is closed")
        return self._sign_canonical(envelope.signing_bytes(), "admission")

    def _sign_canonical(self, value: bytes, operation: str) -> bytes:
        value = sha256(value).digest()
        source = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        size = wintypes.DWORD()
        _status(
            self._cng.ncrypt.NCryptSignHash(
                wintypes.HANDLE(self._handle),
                None,
                source,
                len(value),
                None,
                0,
                ctypes.byref(size),
                0,
            ),
            "size signature",
        )
        output = (ctypes.c_ubyte * size.value)()
        _status(
            self._cng.ncrypt.NCryptSignHash(
                wintypes.HANDLE(self._handle),
                None,
                source,
                len(value),
                output,
                size.value,
                ctypes.byref(size),
                0,
            ),
            f"sign {operation}",
        )
        return bytes(output[: size.value])

    def public_blob(self) -> bytes:
        if not self._handle:
            raise AuthorityServiceError("CNG signing key is closed")
        size = wintypes.DWORD()
        _status(
            self._cng.ncrypt.NCryptExportKey(
                wintypes.HANDLE(self._handle),
                None,
                BCRYPT_ECCPUBLIC_BLOB,
                None,
                None,
                0,
                ctypes.byref(size),
                0,
            ),
            "size public key",
        )
        output = (ctypes.c_ubyte * size.value)()
        _status(
            self._cng.ncrypt.NCryptExportKey(
                wintypes.HANDLE(self._handle),
                None,
                BCRYPT_ECCPUBLIC_BLOB,
                None,
                output,
                size.value,
                ctypes.byref(size),
                0,
            ),
            "export public key",
        )
        return bytes(output[: size.value])

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, 0
            _status(
                self._cng.ncrypt.NCryptFreeObject(wintypes.HANDLE(handle)), "close key"
            )

    def __enter__(self) -> CngAdmissionSigner:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()


class CngAdmissionVerifier:
    def __init__(self, public_blob: bytes) -> None:
        if type(public_blob) is not bytes or not public_blob:
            raise AuthorityServiceError("pinned CNG public key is missing")
        self._blob = public_blob

    def verify_admission(self, admission: SignedAdmission) -> bool:
        cng = _Cng()
        algorithm = wintypes.HANDLE()
        key = wintypes.HANDLE()
        blob = (ctypes.c_ubyte * len(self._blob)).from_buffer_copy(self._blob)
        try:
            _status(
                cng.bcrypt.BCryptOpenAlgorithmProvider(
                    ctypes.byref(algorithm),
                    "ECDSA_P256",
                    None,
                    0,
                ),
                "open ECDSA verifier",
            )
            _status(
                cng.bcrypt.BCryptImportKeyPair(
                    algorithm,
                    None,
                    BCRYPT_ECCPUBLIC_BLOB,
                    ctypes.byref(key),
                    blob,
                    len(self._blob),
                    0,
                ),
                "import public key",
            )
            hashed = sha256(admission.envelope.signing_bytes()).digest()
            source = (ctypes.c_ubyte * len(hashed)).from_buffer_copy(hashed)
            signature = (ctypes.c_ubyte * len(admission.signature)).from_buffer_copy(
                admission.signature
            )
            result: int = cng.bcrypt.BCryptVerifySignature(
                key,
                None,
                source,
                len(hashed),
                signature,
                len(admission.signature),
                0,
            )
            return result == 0
        finally:
            if key.value:
                cng.bcrypt.BCryptDestroyKey(key)
            if algorithm.value:
                cng.bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)


class CngIdentityCapabilitySigner(CngAdmissionSigner):
    """A distinct key handle fenced to exactly one identity capability."""

    def __init__(
        self,
        *,
        capability: str,
        key_handle: int,
        key_id: str,
        key_version: int,
        cng: _Cng | None = None,
    ) -> None:
        if capability not in ("ACCOUNT_IDENTITY_V1", "PORTFOLIO_IDENTITY_V1"):
            raise AuthorityServiceError("unsupported identity signing capability")
        super().__init__(
            key_handle=key_handle, key_id=key_id, key_version=key_version, cng=cng
        )
        self._capability = capability

    @property
    def capability(self) -> str:
        return self._capability

    def sign_admission(self, envelope: AdmissionEnvelope) -> bytes:
        del envelope
        raise AuthorityServiceError("identity key cannot sign operational admissions")

    @classmethod
    def engineering_identity(
        cls, *, dimension: str, key_id: str, key_version: int = 1
    ) -> CngIdentityCapabilitySigner:
        if dimension not in ("ACCOUNT", "PORTFOLIO"):
            raise AuthorityServiceError("invalid identity capability")
        key = CngAdmissionSigner.engineering_ephemeral(
            key_id=key_id, key_version=key_version
        )
        value = cls(
            capability=dimension + "_IDENTITY_V1",
            key_handle=key._handle,
            key_id=key_id,
            key_version=key_version,
            cng=key._cng,
        )
        key._handle = 0
        return value

    @classmethod
    def open_identity(
        cls,
        *,
        dimension: str,
        key_name: str,
        key_id: str,
        key_version: int,
        expected_public_blob: bytes,
    ) -> CngIdentityCapabilitySigner:
        if dimension not in ("ACCOUNT", "PORTFOLIO"):
            raise AuthorityServiceError("invalid identity capability")
        key = CngAdmissionSigner.open_provisioned(
            key_name=key_name,
            key_id=key_id,
            key_version=key_version,
            expected_public_blob=expected_public_blob,
        )
        value = cls(
            capability=dimension + "_IDENTITY_V1",
            key_handle=key._handle,
            key_id=key.key_id,
            key_version=key.key_version,
            cng=key._cng,
        )
        key._handle = 0
        return value

    def sign_identity(self, value: bytes) -> bytes:
        from automated_trading_bot.stage5.identity_scope import (
            parse_canonical,
            assertion_signing_bytes,
            IdentityScopeError,
        )

        expected = (
            b"ATIS-S5-ACCOUNT-IDENTITY-ASSERTION-V1\0"
            if self._capability == "ACCOUNT_IDENTITY_V1"
            else b"ATIS-S5-PORTFOLIO-IDENTITY-ASSERTION-V1\0"
        )
        if (
            not self._handle
            or type(value) is not bytes
            or not value.startswith(expected)
        ):
            raise AuthorityServiceError("cross-capability signing attempt")
        try:
            payload = parse_canonical(value[len(expected) :])
            if (
                assertion_signing_bytes(payload) != value
                or payload["producer_key_id"] != self.key_id
                or payload["producer_key_version"] != self.key_version
            ):
                raise IdentityScopeError("identity key context mismatch")
        except IdentityScopeError as exc:
            raise AuthorityServiceError("invalid identity signing payload") from exc
        return self._sign_canonical(value, self._capability)


class CngIdentityVerifier:
    """Verify-only P256 boundary. No key handles or signing capability."""

    def __init__(self, public_blob: bytes):
        if (
            type(public_blob) is not bytes
            or len(public_blob) != 72
            or public_blob[:8] != b"ECS1\x20\x00\x00\x00"
        ):
            raise AuthorityServiceError("pinned P256 public identity required")
        self._blob = public_blob

    def verify_bytes(self, value: bytes, signature: bytes) -> bool:
        if (
            type(value) is not bytes
            or type(signature) is not bytes
            or len(signature) != 64
        ):
            return False
        cng = _Cng()
        algorithm = wintypes.HANDLE()
        key = wintypes.HANDLE()
        blob = (ctypes.c_ubyte * len(self._blob)).from_buffer_copy(self._blob)
        try:
            _status(
                cng.bcrypt.BCryptOpenAlgorithmProvider(
                    ctypes.byref(algorithm), "ECDSA_P256", None, 0
                ),
                "open identity verifier",
            )
            _status(
                cng.bcrypt.BCryptImportKeyPair(
                    algorithm,
                    None,
                    BCRYPT_ECCPUBLIC_BLOB,
                    ctypes.byref(key),
                    blob,
                    len(self._blob),
                    0,
                ),
                "import identity public key",
            )
            hashed = sha256(value).digest()
            source = (ctypes.c_ubyte * len(hashed)).from_buffer_copy(hashed)
            sig = (ctypes.c_ubyte * len(signature)).from_buffer_copy(signature)
            result: int = cng.bcrypt.BCryptVerifySignature(
                key, None, source, len(hashed), sig, len(signature), 0
            )
            return result == 0
        finally:
            if key.value:
                cng.bcrypt.BCryptDestroyKey(key)
            if algorithm.value:
                cng.bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
