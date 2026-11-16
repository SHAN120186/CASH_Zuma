"""Authenticated credential storage for explicit holding-administrator actions.

This module never changes a login hash, permissions, sessions or audit records.
Callers authorize access and write metadata-only audit events. Plaintext belongs
only in the encrypted envelope and the explicit reveal response.
"""
import base64
import hashlib
import hmac
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from .db import UserCredential, now


class VaultError(Exception):
    """Secret-free failure; callers may expose only the state and this message."""
    MESSAGES = {
        'missing': 'Пароль не сохранён.',
        'stale': 'Сохранённый пароль больше не соответствует учётной записи.',
        'locked': 'Сохранённый пароль сейчас недоступен.',
    }

    def __init__(self, state):
        self.state = state
        super().__init__(self.MESSAGES[state])


def _cipher():
    # No JWT key, database value or ephemeral local file is a valid fallback.
    configured = os.getenv('CREDENTIAL_VAULT_KEY', '').strip()
    if not configured:
        return None
    try:
        encoded = configured.encode('ascii')
        raw = base64.b64decode(encoded, altchars=b'-_', validate=True)
        if len(raw) != 32:
            return None
        return Fernet(encoded), hashlib.sha256(raw).hexdigest()[:16]
    except (ValueError, UnicodeError):
        return None


def configured():
    """Validate configuration without revealing key material."""
    return _cipher() is not None


def _fingerprint(user):
    return hashlib.sha256(user.password_hash.encode('utf-8')).hexdigest()


def forget(s, user):
    """Remove only the recoverable copy; the login password remains unchanged."""
    if user.id is not None:
        row = s.get(UserCredential, user.id)
        if row is not None:
            s.delete(row)


def store(s, user, password, actor_id=None):
    """Encrypt a caller-validated password in the caller's existing transaction.

    Missing/invalid configuration preserves the ordinary password operation and
    removes a previously recoverable copy, which must never become misleading.
    """
    configured = _cipher()
    if configured is None:
        forget(s, user)
        return False
    if not isinstance(password, str) or not password or len(password) > 128:
        raise ValueError('Некорректные данные пароля.')
    if user.id is None:
        s.flush()
    fingerprint = _fingerprint(user)
    cipher, key_id = configured
    envelope = {'v': 1, 'user_id': user.id, 'username': user.username,
                'hash_fingerprint': fingerprint, 'password': password}
    token = cipher.encrypt(json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    row = s.get(UserCredential, user.id)
    if row is None:
        row = UserCredential(user_id=user.id)
        s.add(row)
    row.ciphertext = token
    row.hash_fingerprint = fingerprint
    row.key_id = key_id
    row.updated_by = actor_id
    row.updated_at = now()
    return True


def states(s, users):
    """One metadata query; no ciphertext load or decryption per listed user."""
    users = list(users)
    by_id = {u.id: u for u in users if u.id is not None}
    result = {uid: 'missing' for uid in by_id}
    if not by_id:
        return result
    configured = _cipher()
    key_id = configured[1] if configured is not None else None
    rows = s.execute(select(UserCredential.user_id, UserCredential.hash_fingerprint, UserCredential.key_id)
                     .where(UserCredential.user_id.in_(by_id)))
    for uid, fingerprint, stored_key_id in rows:
        if not hmac.compare_digest(fingerprint, _fingerprint(by_id[uid])):
            result[uid] = 'stale'
        elif key_id is None or not hmac.compare_digest(stored_key_id, key_id):
            result[uid] = 'locked'
        else:
            result[uid] = 'stored'
    return result


def reveal(s, user):
    """Decrypt only a current, identity-bound envelope or fail closed."""
    row = s.get(UserCredential, user.id)
    if row is None:
        raise VaultError('missing')
    fingerprint = _fingerprint(user)
    if not hmac.compare_digest(row.hash_fingerprint, fingerprint):
        raise VaultError('stale')
    configured = _cipher()
    if configured is None or not hmac.compare_digest(row.key_id, configured[1]):
        raise VaultError('locked')
    try:
        payload = json.loads(configured[0].decrypt(row.ciphertext))
        if (not isinstance(payload, dict) or payload.get('v') != 1
                or type(payload.get('user_id')) is not int or payload['user_id'] != user.id
                or payload.get('username') != user.username
                or payload.get('hash_fingerprint') != fingerprint
                or not isinstance(payload.get('password'), str) or not payload['password']
                or len(payload['password']) > 128):
            raise VaultError('locked')
        return payload['password']
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        raise VaultError('locked') from None
