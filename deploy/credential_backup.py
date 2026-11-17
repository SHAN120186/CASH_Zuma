"""Read-only vault verification without application imports or decryption keys."""
import hashlib
from datetime import date, datetime


PRESENCE_SQL = "SELECT to_regclass('user_credentials') IS NOT NULL"
ROWS_SQL = ('SELECT user_id, ciphertext, hash_fingerprint, key_id, updated_by, updated_at '
            'FROM user_credentials ORDER BY user_id')
_PREFIX = b'cash-zuma:user_credentials:backup:v1\0'


def _encoded(value):
    if value is None:
        return b'n'
    if isinstance(value, (bytes, bytearray, memoryview)):
        return b'b' + bytes(value)
    if isinstance(value, (date, datetime)):
        return b't' + value.isoformat().encode('utf-8')
    return b's' + str(value).encode('utf-8')


def credential_snapshot(connection):
    """Return presence, count and one digest; never return individual vault rows."""
    if not connection.execute(PRESENCE_SQL).fetchone()[0]:
        return {'present': False, 'count': 0, 'sha256': None}
    digest = hashlib.sha256(_PREFIX)
    count = 0
    for row in connection.execute(ROWS_SQL):
        for value in row:
            encoded = _encoded(value)
            digest.update(len(encoded).to_bytes(8, 'big'))
            digest.update(encoded)
        count += 1
    return {'present': True, 'count': count, 'sha256': digest.hexdigest()}


def credential_snapshots_match(source, restored, *, allow_new_empty=False):
    """Only schema migration may add an empty vault to a source without one."""
    if source == restored:
        return True
    return (allow_new_empty and source == {'present': False, 'count': 0, 'sha256': None}
            and restored == {'present': True, 'count': 0,
                             'sha256': hashlib.sha256(_PREFIX).hexdigest()})
