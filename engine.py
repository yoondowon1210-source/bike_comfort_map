"""자전거 편의 지도 - 경로 계산 엔진 (Streamlit 없이도 동작)"""
import math
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

LAT0 = 37.55
KX = 111320 * math.cos(math.radians(LAT0))   # 경도 1도 ≈ m
KY = 110540                                   # 위도 1도 ≈ m
BIG_ROADS = {"primary", "primary_link", "secondary", "secondary_link", "trunk", "trunk_link"}

# 편안함 점수 구간별 색 (지도 표시용)
COMFORT_BINS = [
    (1.0, "#1a9850", "아주 편함"),
    (0.0, "#91cf60", "편함"),
    (-2.0, "#fee08b", "보통"),
    (-4.0, "#fc8d59", "불편"),
    (-99, "#d73027", "매우 불편"),
]


def comfort_bin(c):
    for i, (th, _, _) in enumerate(COMFORT_BINS):
        if c >= th:
            return i
    return len(COMFORT_BINS) - 1


class BikeGraph:
    def __init__(self, graph_path, geom_path):
        g = np.load(graph_path)
        m = np.load(geom_path)
        self.U, self.V = g["U"].astype(np.int64), g["V"].astype(np.int64)
        self.X, self.Y, self.Z = g["X"], g["Y"], g["Z"].astype(np.float64)
        self.N = len(self.X)
        self.length = g["length"].astype(np.float64)
        self.t = g["t"].astype(np.float64)
        self.comfort = g["comfort"].astype(np.float64)
        self.slope, self.stype = g["slope"], g["stype"]
        self.pave, self.traffic, self.spass = g["pave"], g["traffic"], g["spass"]
        self.grade = g["grade"].astype(np.float64)
        hw_list = [str(h) for h in g["hw_list"]]
        self.hw = np.array(hw_list, dtype=object)[g["hw_code"]]
        self.name_list = [str(n) for n in g["name_list"]]
        self.name_code = g["name_code"]
        self.infra = g["sep"] | g["paint"] | (self.hw == "cycleway")
        self.big = np.isin(self.hw, list(BIG_ROADS))
        self.tunnel = self.spass <= -4
        self.cq, self.off, self.origin = m["cq"], m["off"], m["origin"]
        self.tree = cKDTree(np.c_[self.X * KX, self.Y * KY])
        self._cache = {}

    # ---------- 기본 도구 ----------
    def nearest(self, lat, lon):
        d, i = self.tree.query([lon * KX, lat * KY])
        return int(i), float(d)

    def edge_coords(self, e):
        a, b = self.off[e], self.off[e + 1]
        return self.cq[a:b] / 1e5 + self.origin   # (lon, lat)

    def _graph(self, lam):
        """lam: 편안함 비중(0~1). 비용 = 시간 × (1 + 4·lam·불편도)"""
        key = round(lam, 2)
        if key in self._cache:
            return self._cache[key]
        q = (4.0 - np.clip(self.comfort, -8, 4)) / 12.0        # 0(최고)~1(최악)
        cost = self.t * (1.0 + 4.0 * lam * q)
        # 같은 (u,v) 사이 여러 도로가 있으면 가장 싼 것만
        order = np.lexsort((cost, self.V, self.U))
        u, v = self.U[order], self.V[order]
        first = np.r_[True, (u[1:] != u[:-1]) | (v[1:] != v[:-1])]
        sel = order[first]
        keys = self.U[sel] * self.N + self.V[sel]
        k_order = np.argsort(keys)
        sel, keys = sel[k_order], keys[k_order]
        A = csr_matrix((cost[sel], (self.U[sel], self.V[sel])), shape=(self.N, self.N))
        if len(self._cache) > 6:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (A, keys, sel)
        return self._cache[key]

    def _leg(self, s, t, lam):
        A, keys, sel = self._graph(lam)
        _, pred = dijkstra(A, indices=s, return_predecessors=True, min_only=False)
        if s != t and pred[t] < 0:
            return None
        nodes = [t]
        while nodes[-1] != s:
            nodes.append(int(pred[nodes[-1]]))
        nodes = np.array(nodes[::-1], dtype=np.int64)
        k = nodes[:-1] * self.N + nodes[1:]
        edges = sel[np.searchsorted(keys, k)]
        return nodes, edges

    # ---------- 경로 계산 ----------
    def route(self, points, lam):
        """points: [(lat, lon), ...] 출발 → 경유지 → 도착"""
        snapped = [self.nearest(*p) for p in points]
        all_nodes, all_edges = [], []
        for (a, _), (b, _) in zip(snapped[:-1], snapped[1:]):
            leg = self._leg(a, b, lam)
            if leg is None:
                return None
            n, e = leg
            all_nodes.append(n if not all_nodes else n[1:])
            all_edges.append(e)
        nodes = np.concatenate(all_nodes)
        edges = np.concatenate(all_edges) if all_edges else np.array([], dtype=np.int64)
        return Route(self, nodes, edges, snapped, lam)


class Route:
    def __init__(self, G, nodes, edges, snapped, lam):
        self.G, self.nodes, self.edges, self.snapped, self.lam = G, nodes, edges, snapped, lam

    def stats(self):
        G, e = self.G, self.edges
        L = G.length[e]
        tot = L.sum() if len(e) else 0.0
        dz = G.Z[G.V[e]] - G.Z[G.U[e]]
        w = (lambda mask: float(L[mask].sum() / tot * 100) if tot else 0.0)
        long_ = L >= 30
        maxg = float(np.abs(G.grade[e][long_]).max() * 100) if long_.any() else 0.0
        return {
            "거리 (km)": round(tot / 1000, 2),
            "예상 시간 (분)": round(G.t[e].sum() / 60, 1),
            "누적 오르막 (m)": round(float(dz[dz > 0].sum()), 0),
            "최대 경사 (%)": round(min(maxg, 30), 1),
            "자전거 인프라 (%)": round(w(G.infra[e]), 0),
            "큰길 비율 (%)": round(w(G.big[e]), 0),
            "평균 편안함 점수": round(float((G.comfort[e] * L).sum() / tot), 2) if tot else 0.0,
            "터널 통과 (곳)": int(_count_runs(G.tunnel[e])),
        }

    def coords(self):
        """경로 전체 좌표 [(lon, lat), ...]"""
        parts = [self.G.edge_coords(e) for e in self.edges]
        return np.vstack(parts) if parts else np.zeros((0, 2))

    def colored_segments(self):
        """편안함 구간별로 묶은 선 [(색, 라벨, [(lat,lon)...]), ...]"""
        segs, cur_bin, cur = [], None, []
        for e in self.edges:
            b = comfort_bin(self.G.comfort[e])
            c = self.G.edge_coords(e)[:, ::-1].tolist()
            if b != cur_bin and cur:
                segs.append((cur_bin, cur))
                cur = [cur[-1]]
            cur_bin = b
            cur.extend(c if not cur else c[1:])
        if cur:
            segs.append((cur_bin, cur))
        return [(COMFORT_BINS[b][1], COMFORT_BINS[b][2], pts) for b, pts in segs]

    def profile(self):
        """경사 단면: 누적거리(km), 고도(m)"""
        G = self.G
        d = np.r_[0, np.cumsum(G.length[self.edges])] / 1000
        return pd.DataFrame({"거리 (km)": d, "고도 (m)": G.Z[self.nodes]})

    def steps(self):
        """턴바이턴 안내 목록"""
        G = self.G
        groups = []   # [name, length, first_edge, last_edge]
        for e in self.edges:
            nm = G.name_list[G.name_code[e]] or ""
            if groups and groups[-1][0] == nm:
                groups[-1][1] += G.length[e]
                groups[-1][3] = e
            else:
                groups.append([nm, G.length[e], e, e])
        # 짧은 이름 없는 조각은 앞 구간에 합치기
        merged = []
        for g in groups:
            if merged and (g[1] < 25 and g[0] == ""):
                merged[-1][1] += g[1]
                merged[-1][3] = g[3]
            elif merged and merged[-1][0] == g[0]:
                merged[-1][1] += g[1]
                merged[-1][3] = g[3]
            else:
                merged.append(list(g))
        out = []
        for i, (nm, L, fe, le) in enumerate(merged):
            c = G.edge_coords(fe)
            label = nm if nm else "이름 없는 길"
            if i == 0:
                act = "출발"
            else:
                prev = G.edge_coords(merged[i - 1][3])
                act = _turn(_bearing(prev[-2], prev[-1]), _bearing(c[0], c[min(1, len(c) - 1)]))
            out.append({
                "no": i + 1, "action": act, "road": label, "dist_m": float(L),
                "lat": float(c[0][1]), "lon": float(c[0][0]),
                "bearing": _bearing(c[0], c[-1]) if len(c) > 1 else 0.0,
            })
        last = G.edge_coords(self.edges[-1])[-1] if len(self.edges) else None
        if last is not None:
            out.append({"no": len(out) + 1, "action": "도착", "road": "", "dist_m": 0.0,
                        "lat": float(last[1]), "lon": float(last[0]), "bearing": 0.0})
        return out

    def gpx(self, name="자전거 편의 지도 경로"):
        pts = self.coords()
        z = np.interp(np.linspace(0, len(self.nodes) - 1, len(pts)), np.arange(len(self.nodes)), self.G.Z[self.nodes])
        body = "\n".join(f'      <trkpt lat="{la:.6f}" lon="{lo:.6f}"><ele>{h:.1f}</ele></trkpt>'
                         for (lo, la), h in zip(pts, z))
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<gpx version="1.1" creator="bike-comfort-map" xmlns="http://www.topografix.com/GPX/1/1">\n'
                f'  <trk><name>{name}</name><trkseg>\n{body}\n  </trkseg></trk>\n</gpx>\n')


# ---------- 보조 함수 ----------
def _count_runs(mask):
    mask = np.asarray(mask, dtype=bool)
    return int(np.sum(mask & ~np.r_[False, mask[:-1]])) if len(mask) else 0


def _bearing(p, q):
    dx = (q[0] - p[0]) * KX
    dy = (q[1] - p[1]) * KY
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def _turn(b1, b2):
    d = (b2 - b1 + 540) % 360 - 180   # -180~180, +면 오른쪽
    a = abs(d)
    if a < 25:
        return "직진"
    side = "우" if d > 0 else "좌"
    if a < 60:
        return f"약간 {side}측"
    if a < 150:
        return f"{side}회전"
    return "유턴"


def load_places(path):
    p = pd.read_csv(path)
    p = p.dropna(subset=["name"])
    p["name"] = p["name"].astype(str).str.strip()
    return p.drop_duplicates(subset=["name", "gu"]).reset_index(drop=True)


def search_places(places, q, limit=10):
    import difflib
    q = q.strip()
    if not q:
        return places.iloc[0:0]
    qn = q.replace(" ", "")
    names = places["name"].str.replace(" ", "", regex=False)
    starts = names.str.startswith(qn)
    contains = names.str.contains(qn, regex=False)
    if qn.endswith("역") and len(qn) > 1:      # "노량진역" → 이름 "노량진" + 종류 "역"
        stem = (names == qn[:-1]) & (places["kind"] == "역")
        starts, contains = starts | stem, contains | stem
    hit = places[starts | contains].copy()
    hit["_r"] = np.where(starts[starts | contains], 0, 1)
    hit = hit.sort_values(["_r", "name"])
    if len(hit) < limit:
        close = difflib.get_close_matches(qn, names.tolist(), n=limit, cutoff=0.6)
        extra = places[names.isin(close) & ~places.index.isin(hit.index)]
        hit = pd.concat([hit, extra])
    return hit.head(limit).drop(columns="_r", errors="ignore")
