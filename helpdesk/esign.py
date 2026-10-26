"""
Validation helpers for ticket e-signatures.

The signature arrives from the browser as a PNG data URL drawn on a <canvas>.
It is untrusted input that is later rendered inside an <img src="..."> tag, so
we strictly verify it is a real, reasonably sized PNG rather than trusting it.
"""
import base64
import binascii
import io
import re

MAX_SIGNATURE_BYTES = 300 * 1024          # 300 KB decoded
MAX_DIMENSION = 1200                      # px
_DATA_URL = re.compile(r'^data:image/png;base64,([A-Za-z0-9+/=]+)$')


def clean_signature(data_url):
    """
    Return (clean_data_url, error_message). Exactly one of the two is None.
    """
    data_url = (data_url or '').strip()
    if not data_url:
        return None, 'Please draw your signature in the box.'

    m = _DATA_URL.match(data_url)
    if not m:
        return None, 'The signature was not recognised. Please draw it again.'

    try:
        raw = base64.b64decode(m.group(1), validate=True)
    except (binascii.Error, ValueError):
        return None, 'The signature was corrupted. Please draw it again.'

    if len(raw) > MAX_SIGNATURE_BYTES:
        return None, 'The signature image is too large. Please clear and redraw it.'

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img.verify()
        if img.format != 'PNG':
            raise ValueError('not png')
        w, h = img.size
        if w > MAX_DIMENSION or h > MAX_DIMENSION:
            return None, 'The signature image is too large. Please clear and redraw it.'
        # A blank canvas is a valid PNG; make sure something was actually drawn.
        img = Image.open(io.BytesIO(raw)).convert('RGBA')
        if not img.getchannel('A').getbbox():
            return None, 'Please draw your signature in the box.'
    except Exception:
        return None, 'The signature was not recognised. Please draw it again.'

    return data_url, None
