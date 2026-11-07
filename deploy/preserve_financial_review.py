"""Preserve a previously reviewed API result, without parsing its Excel source.

Run only by an operator with database access. Input is base64/zlib JSON on stdin;
the SHA256 of the original JSON must be supplied independently. Never commit
the input, put it in a public URL, or accept it through an application endpoint.
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Match the service startup driver without printing its connection string.
if os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')):
    os.environ['DATABASE_URL'] = os.environ['DATABASE_URL'].replace(
        'postgres://', 'postgresql+psycopg://', 1).replace('postgresql://', 'postgresql+psycopg://', 1)

from app.db import FinancialStatementSource, unit
from app.financial_statements import save_ready_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(sys.stdin.read().strip(), validate=True), 20 * 1024 * 1024 + 1)
    if not decoder.eof or decoder.unused_data:
        raise SystemExit('Invalid or oversized report; nothing saved.')
    if len(raw) > 20 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != args.sha256:
        raise SystemExit('Report checksum or size mismatch; nothing saved.')
    payload = json.loads(raw)
    review = payload['source']
    if payload['company_id'] != review['company_id'] or payload['selected'] != review['id']:
        raise SystemExit('Report scope mismatch; nothing saved.')
    with unit(True) as s:
        source = s.get(FinancialStatementSource, review['id'])
        if source is None or source.company_id != review['company_id']:
            raise SystemExit('Source not found; nothing saved.')
        save_ready_review(s, source, review)
    print('Prepared report preserved; source files and financial operations unchanged.')


if __name__ == '__main__':
    main()
