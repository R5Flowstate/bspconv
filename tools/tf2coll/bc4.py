"""BC4 / BC5 (unorm) block encoder and decoder, vectorised over blocks."""
import numpy as np


def _palette(r0, r1):
    r0 = r0.astype(np.float64)
    r1 = r1.astype(np.float64)
    pal = np.empty(r0.shape + (8,))
    pal[..., 0] = r0
    pal[..., 1] = r1
    eight = r0 > r1
    for i in range(2, 8):
        pal[..., i] = ((8 - i) * r0 + (i - 1) * r1) / 7
    six = np.empty_like(pal)
    six[..., 0] = r0
    six[..., 1] = r1
    for i in range(2, 6):
        six[..., i] = ((6 - i) * r0 + (i - 1) * r1) / 5
    six[..., 6] = 0
    six[..., 7] = 255
    return np.where(eight[..., None], pal, six)


def _fit(vals, r0, r1):
    """Nearest palette index per texel -> (indices, squared error per block)."""
    pal = _palette(r0, r1)
    d = (vals[..., :, None].astype(np.float64) - pal[..., None, :]) ** 2
    idx = d.argmin(-1)
    return idx, np.take_along_axis(d, idx[..., None], -1)[..., 0].sum(-1)


def encode_bc4(vals):
    """vals: (N, 16) uint8 texels in row-major block order -> (N, 8) uint8 blocks."""
    v = vals.astype(np.int32)
    lo, hi = v.min(1), v.max(1)
    inner = (v > 0) & (v < 255)
    ilo = np.where(inner, v, 255).min(1)
    ihi = np.where(inner, v, 0).max(1)
    ilo, ihi = np.minimum(ilo, ihi), np.maximum(ilo, ihi)

    cands = []
    for a in range(3):
        for b in range(3):
            r0, r1 = np.clip(hi - a, 0, 255), np.clip(lo + b, 0, 255)
            ok = r0 > r1
            cands.append((np.where(ok, r0, hi), np.where(ok, r1, lo)))
            s0, s1 = np.clip(ilo + a, 0, 255), np.clip(ihi - b, 0, 255)
            ok = s0 <= s1
            cands.append((np.where(ok, s0, ilo), np.where(ok, s1, ihi)))

    best_err = np.full(len(v), np.inf)
    best_r0 = np.zeros(len(v), np.int32)
    best_r1 = np.zeros(len(v), np.int32)
    best_idx = np.zeros((len(v), 16), np.int64)
    for r0, r1 in cands:
        idx, err = _fit(v, r0, r1)
        take = err < best_err
        best_err = np.where(take, err, best_err)
        best_r0 = np.where(take, r0, best_r0)
        best_r1 = np.where(take, r1, best_r1)
        best_idx[take] = idx[take]

    # Endpoint descent on the blocks that are not exact yet; a move never changes the mode.
    live = np.nonzero(best_err > 0)[0]
    for step in (16, 8, 4, 2, 1, 1):
        if not len(live):
            break
        sub = v[live]
        for d0, d1 in ((step, 0), (-step, 0), (0, step), (0, -step), (step, step), (-step, -step),
                       (step, -step), (-step, step)):
            c0, c1 = best_r0[live], best_r1[live]
            r0, r1 = np.clip(c0 + d0, 0, 255), np.clip(c1 + d1, 0, 255)
            same_mode = (r0 > r1) == (c0 > c1)
            idx, err = _fit(sub, r0, r1)
            take = same_mode & (err < best_err[live])
            rows = live[take]
            best_err[rows] = err[take]
            best_r0[rows] = r0[take]
            best_r1[rows] = r1[take]
            best_idx[rows] = idx[take]
        live = live[best_err[live] > 0]

    bits = np.zeros(len(v), np.uint64)
    for i in range(16):
        bits |= best_idx[:, i].astype(np.uint64) << np.uint64(3 * i)
    out = np.empty((len(v), 8), np.uint8)
    out[:, 0] = best_r0
    out[:, 1] = best_r1
    for k in range(6):
        out[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(0xFF)).astype(np.uint8)
    return out


def decode_bc4(blocks):
    """(N, 8) uint8 blocks -> (N, 16) texel values (float, unrounded)."""
    bits = np.zeros(len(blocks), np.uint64)
    for k in range(6):
        bits |= blocks[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    idx = np.stack([((bits >> np.uint64(3 * i)) & np.uint64(7)).astype(np.int64) for i in range(16)], 1)
    return np.take_along_axis(_palette(blocks[:, 0], blocks[:, 1]), idx, 1)


def to_blocks(img):
    """(H, W) image, H and W multiples of 4 -> (H/4 * W/4, 16) row-major blocks."""
    h, w = img.shape
    return img.reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16)


def encode_bc5(r, g):
    """Two (H, W) uint8 channels -> BC5 blocks (red block then green block), row-major.
    The image is edge-padded to whole blocks."""
    h, w = r.shape
    ph, pw = (-h) % 4, (-w) % 4
    r = np.pad(r, ((0, ph), (0, pw)), mode="edge")
    g = np.pad(g, ((0, ph), (0, pw)), mode="edge")
    return np.concatenate([encode_bc4(to_blocks(r)), encode_bc4(to_blocks(g))], 1)
