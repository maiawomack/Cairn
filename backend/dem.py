"""Elevation lookups from AWS Terrain Tiles (terrarium PNGs), the same DEM the map uses.

Used by the offline build scripts, never by the real time path. Tiles are
downloaded once into data/dem_cache/. PNG decoding is plain Python (zlib).
"""

import math
import struct
import urllib.request
import zlib
from pathlib import Path

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ZOOM = 15  # highest zoom the tiles come in, about 3 m per pixel here
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "dem_cache"
USER_AGENT = "Cairn-hackathon/0.1 (ski safety demo)"

_tiles = {}


def _read_png_rgb(data):
    """Decode an 8 bit RGB or RGBA PNG into rows of bytes."""
    pos, idat = 8, b""
    while pos < len(data):
        length, chunk_type = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if chunk_type == b"IHDR":
            width, height, _, color_type = struct.unpack(">IIBB", body[:10])
        elif chunk_type == b"IDAT":
            idat += body
        pos += 12 + length
    bpp = 4 if color_type == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows, previous, i = [], bytearray(stride), 0
    for _ in range(height):
        filter_type, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for k in range(stride):
            a = line[k - bpp] if k >= bpp else 0
            b = previous[k]
            c = previous[k - bpp] if k >= bpp else 0
            if filter_type == 1:
                line[k] = (line[k] + a) & 255
            elif filter_type == 2:
                line[k] = (line[k] + b) & 255
            elif filter_type == 3:
                line[k] = (line[k] + (a + b) // 2) & 255
            elif filter_type == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[k] = (line[k] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        previous = line
    return rows, bpp


def _tile(x, y):
    if (x, y) not in _tiles:
        path = CACHE_DIR / f"{ZOOM}_{x}_{y}.png"
        if not path.exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(TILE_URL.format(z=ZOOM, x=x, y=y), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                path.write_bytes(response.read())
        _tiles[(x, y)] = _read_png_rgb(path.read_bytes())
    return _tiles[(x, y)]


def elevation(lon, lat):
    """Elevation in meters at a point (nearest DEM pixel)."""
    n = 2 ** ZOOM
    fx = (lon + 180) / 360 * n
    fy = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    rows, bpp = _tile(int(fx), int(fy))
    px, py = int((fx % 1) * 256), int((fy % 1) * 256)
    r, g, b = rows[py][px * bpp:px * bpp + 3]
    return r * 256 + g + b / 256 - 32768
