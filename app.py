"""서울 자전거 편의 지도 - Streamlit 앱"""
import json
from pathlib import Path

import altair as alt
import folium
import numpy as np
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation, streamlit_js_eval

from engine import COMFORT_BINS, BikeGraph, load_places, search_places

DATA = Path(__file__).parent / "data"
st.set_page_config(page_title="서울 자전거 편의 지도", page_icon="🚲", layout="wide")

SEOUL = (37.5665, 126.9780)
EXAMPLE = ({"lat": 37.601739, "lon": 126.954885, "label": "상명대학교 서울캠퍼스"},
           {"lat": 37.514238, "lon": 126.942040, "label": "노량진역"})


def secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


KAKAO_KEY = secret("KAKAO_REST_KEY")
FEEDBACK_URL = secret("FEEDBACK_URL")


# ---------------- 데이터 ----------------
@st.cache_resource(show_spinner="지도 데이터를 불러오는 중이에요…")
def load_data():
    return BikeGraph(DATA / "app_graph.npz", DATA / "app_geom.npz"), load_places(DATA / "places.csv")


@st.cache_resource(max_entries=64, show_spinner=False)
def get_route(pts, lam):
    return G.route(list(pts), lam)


@st.cache_data(ttl=3600, show_spinner=False)
def kakao_search(q):
    if not KAKAO_KEY:
        return pd.DataFrame()
    try:
        r = requests.get("https://dapi.kakao.com/v2/local/search/keyword.json",
                         headers={"Authorization": f"KakaoAK {KAKAO_KEY}"},
                         params={"query": q, "size": 10}, timeout=3)
        docs = r.json().get("documents", [])
    except Exception:
        return pd.DataFrame()
    rows = [{"name": d["place_name"], "kind": d.get("category_group_name") or "장소",
             "lat": float(d["y"]), "lon": float(d["x"]),
             "gu": (d.get("address_name", "").split() + ["", ""])[1]}
            for d in docs if d.get("address_name", "").startswith("서울")]
    return pd.DataFrame(rows)


G, PLACES = load_data()
ss = st.session_state
for k, v in {"origin": None, "dest": None, "wps": [], "agreed": False, "last_click": None,
             "step": 0, "my_loc": None, "geo_req": 0, "geo_wait": False,
             "favs": None, "fav_write": 0, "url_loaded": False,
             "comfort_pct": 70}.items():
    ss.setdefault(k, v)


# ---------------- 링크(URL)로 경로 불러오기 ----------------
def parse_pt(s):
    try:
        la, lo = map(float, s.split(","))
        return la, lo
    except Exception:
        return None


if not ss.url_loaded:
    qp = st.query_params
    o, d = parse_pt(qp.get("o", "")), parse_pt(qp.get("d", ""))
    if o:
        ss.origin = {"lat": o[0], "lon": o[1], "label": qp.get("ol", "저장된 출발지")}
    if d:
        ss.dest = {"lat": d[0], "lon": d[1], "label": qp.get("dl", "저장된 도착지")}
    for i, w in enumerate(qp.get("w", "").split(";")):
        p = parse_pt(w)
        if p:
            ss.wps.append({"lat": p[0], "lon": p[1], "label": f"경유지 {i + 1}"})
    if qp.get("c", "").isdigit():
        ss["comfort_pct"] = min(100, int(qp["c"]))
    ss.url_loaded = True


# ---------------- 주의사항 팝업 ----------------
@st.dialog("⚠️ 이용 전 꼭 확인해 주세요", dismissible=False)
def notice():
    st.markdown(
        "- 🚫 **주행 중에는 절대 휴대폰을 조작하지 마세요.**\n"
        "- 🗺️ 이 지도는 **출발 전 경로를 미리 익히는 용도**로만 사용해 주세요.\n"
        "- 🚦 실제 도로 상황(공사, 통제, 신호)과 다를 수 있어요. 항상 현장 상황과 교통법규를 우선해 주세요.\n"
        "- 📊 경로 점수는 공개 데이터(OSM, 고도, 자전거도로)로 계산한 **참고용**이에요."
    )
    if st.button("확인했어요", type="primary", width="stretch"):
        ss.agreed = True
        st.rerun()


if not ss.agreed:
    notice()


# ---------------- 공통 도구 ----------------
def set_point(target, lat, lon, label):
    p = {"lat": float(lat), "lon": float(lon), "label": label}
    if target == "출발지":
        ss.origin = p
    elif target == "도착지":
        ss.dest = p
    else:
        if len(ss.wps) < 3:
            ss.wps.append(p)
        else:
            st.toast("경유지는 최대 3곳까지 추가할 수 있어요.")
    ss.step = 0


def pt_label(p):
    return p["label"] if p else "—"


def save_favs():
    ss.fav_write += 1
    streamlit_js_eval(js_expressions=f"localStorage.setItem('bike_favs', {json.dumps(json.dumps(ss.favs, ensure_ascii=False))})",
                      key=f"favw{ss.fav_write}")


# ================= 화면 =================
st.title("🚲 서울 자전거 편의 지도")
st.caption("경사·도로 유형·포장·교통량(차로·속도)·터널을 반영해 **편한 자전거 경로**를 찾아드려요. "
           "🚫 주행 중 조작 금지 · 출발 전 경로 숙지용")

left, right = st.columns([1, 1.9], gap="large")

# ---------------- 왼쪽: 입력 ----------------
with left:
    st.subheader("① 출발지 · 도착지")
    st.markdown(f"🟢 **출발** {pt_label(ss.origin)}  \n"
                + "".join(f"🔵 **경유 {i + 1}** {w['label']}  \n" for i, w in enumerate(ss.wps))
                + f"🔴 **도착** {pt_label(ss.dest)}")

    # 장소 검색
    q = st.text_input("🔎 장소 검색", placeholder="예: 상명대학교, 노량진역, 여의도공원")
    if q:
        res = pd.concat([kakao_search(q), search_places(PLACES, q)], ignore_index=True)
        res = res.drop_duplicates(subset=["name", "gu"]).head(10)
        if res.empty:
            st.info("검색 결과가 없어요. 지도에서 직접 클릭해 보세요.")
        else:
            opts = list(range(len(res)))
            pick = st.selectbox("검색 결과", opts,
                                format_func=lambda i: f"{res.iloc[i]['name']} · {res.iloc[i]['kind']} · {res.iloc[i]['gu']}")
            r = res.iloc[pick]
            b1, b2, b3 = st.columns(3)
            for col, tgt in zip((b1, b2, b3), ("출발지", "도착지", "경유지")):
                if col.button(f"{tgt}로", key=f"s_{tgt}", width="stretch"):
                    set_point(tgt, r["lat"], r["lon"], r["name"])
                    st.rerun()

    click_target = st.radio("🖱️ 지도를 클릭하면", ["출발지", "도착지", "경유지"], horizontal=True,
                            format_func=lambda x: f"{x} 지정" if x != "경유지" else "경유지 추가")

    # 현위치
    g1, g2 = st.columns(2)
    if g1.button("📍 내 위치 가져오기", width="stretch"):
        ss.geo_req += 1
        ss.geo_wait = True
    if ss.geo_wait:
        loc = get_geolocation(component_key=f"geo{ss.geo_req}")
        if loc is None:
            st.caption("위치 확인 중… 브라우저에서 위치 권한을 허용해 주세요.")
        else:
            ss.geo_wait = False
            if isinstance(loc, dict) and "coords" in loc:
                c = loc["coords"]
                ss.my_loc = {"lat": c["latitude"], "lon": c["longitude"], "acc": c.get("accuracy")}
                st.rerun()
            else:
                st.warning("위치를 가져오지 못했어요. 권한 설정을 확인해 주세요.")
    if ss.my_loc:
        if g2.button("현위치를 출발지로", width="stretch"):
            set_point("출발지", ss.my_loc["lat"], ss.my_loc["lon"], "내 위치")
            st.rerun()
        acc = ss.my_loc.get("acc")
        st.caption(f"내 위치 확인됨" + (f" (오차 약 {acc:.0f}m)" if acc else ""))

    # 경유지 관리
    for i, w in enumerate(ss.wps):
        c1, c2 = st.columns([4, 1])
        c1.caption(f"🔵 경유 {i + 1}: {w['label']}")
        if c2.button("✕", key=f"rmw{i}"):
            ss.wps.pop(i)
            st.rerun()

    h1, h2, h3 = st.columns(3)
    if h1.button("↕️ 바꾸기", width="stretch", help="출발지와 도착지를 바꿔요"):
        ss.origin, ss.dest = ss.dest, ss.origin
        ss.wps = ss.wps[::-1]
        st.rerun()
    if h2.button("🔄 초기화", width="stretch"):
        ss.origin, ss.dest, ss.wps, ss.step = None, None, [], 0
        st.query_params.clear()
        st.rerun()
    if h3.button("✨ 예시", width="stretch", help="상명대 → 노량진역"):
        ss.origin, ss.dest, ss.wps = dict(EXAMPLE[0]), dict(EXAMPLE[1]), []
        st.rerun()

    st.subheader("② 경로 기준")
    comfort_pct = st.slider("편안함 비중 (%)", 0, 100, step=10, key="comfort_pct",
                            help="0% = 가장 빠른 길, 100% = 시간이 좀 더 걸려도 가장 편한 길")
    lam = comfort_pct / 100

    # 자주 가는 곳 (브라우저에만 저장)
    with st.expander("⭐ 자주 가는 곳"):
        if ss.favs is None:
            v = streamlit_js_eval(js_expressions="localStorage.getItem('bike_favs') || '[]'", key="favs_load")
            if v is not None:
                try:
                    ss.favs = json.loads(v)
                except Exception:
                    ss.favs = []
        favs = ss.favs or []
        st.caption("이 기기의 브라우저에만 저장돼요. 서버에는 저장되지 않아요.")
        fname = st.text_input("이름", placeholder="예: 학교, 집", key="fav_name")
        f1, f2 = st.columns(2)
        for col, src, lab in ((f1, ss.origin, "출발지"), (f2, ss.dest, "도착지")):
            if col.button(f"현재 {lab} 저장", width="stretch", disabled=src is None or ss.favs is None):
                ss.favs = favs + [{"name": fname or src["label"], "lat": src["lat"], "lon": src["lon"]}]
                save_favs()
                st.rerun()
        for i, f in enumerate(favs):
            a, b, c, d = st.columns([3, 1.2, 1.2, 0.8])
            a.write(f"⭐ {f['name']}")
            if b.button("출발", key=f"fo{i}"):
                set_point("출발지", f["lat"], f["lon"], f["name"]); st.rerun()
            if c.button("도착", key=f"fd{i}"):
                set_point("도착지", f["lat"], f["lon"], f["name"]); st.rerun()
            if d.button("✕", key=f"fx{i}"):
                ss.favs = favs[:i] + favs[i + 1:]
                save_favs()
                st.rerun()
        st.caption("💡 경로를 찾은 뒤 **주소창 링크를 북마크**하면 같은 경로를 바로 다시 열 수 있어요.")

    if FEEDBACK_URL:
        st.link_button("📝 경로 피드백 남기기", FEEDBACK_URL, width="stretch")


# ---------------- 경로 계산 ----------------
routes = {}
if ss.origin and ss.dest:
    pts = tuple([(ss.origin["lat"], ss.origin["lon"])] + [(w["lat"], w["lon"]) for w in ss.wps]
                + [(ss.dest["lat"], ss.dest["lon"])])
    rc, rf = get_route(pts, lam), get_route(pts, 0.0)
    if rc is None or rf is None:
        st.error("경로를 찾지 못했어요. 지점을 도로 가까이로 옮겨 보세요.")
    else:
        routes = {"comfort": rc, "fast": rf}
        far = [round(d) for _, d in rc.snapped if d > 300]
        if far:
            st.warning(f"선택한 지점이 가장 가까운 도로에서 {max(far)}m 떨어져 있어요. 서울 밖이거나 도로가 없는 곳일 수 있어요.")
        # 주소창 링크 갱신 (즐겨찾기/공유용)
        new_qp = {"o": f"{ss.origin['lat']:.6f},{ss.origin['lon']:.6f}", "ol": ss.origin["label"],
                  "d": f"{ss.dest['lat']:.6f},{ss.dest['lon']:.6f}", "dl": ss.dest["label"], "c": str(comfort_pct)}
        if ss.wps:
            new_qp["w"] = ";".join(f"{w['lat']:.6f},{w['lon']:.6f}" for w in ss.wps)
        if dict(st.query_params) != new_qp:
            st.query_params.from_dict(new_qp)


def route_3d(route, ex, zmin, rgb=None):
    data = []
    for e in route.edges:
        c = G.edge_coords(e)
        z = np.linspace(G.Z[G.U[e]], G.Z[G.V[e]], len(c))
        col = rgb or _hex(COMFORT_BINS[_bin(G.comfort[e])][1])
        data.append({"path": [[float(x), float(y), float((h - zmin) * ex + 3)] for (x, y), h in zip(c, z)],
                     "color": col, "label": COMFORT_BINS[_bin(G.comfort[e])][2]})
    return data


def _hex(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def _bin(c):
    for i, (th, _, _) in enumerate(COMFORT_BINS):
        if c >= th:
            return i
    return len(COMFORT_BINS) - 1


def legend():
    st.markdown(" ".join(f"<span style='color:{c};font-size:20px'>■</span> {lab}" for _, c, lab in COMFORT_BINS)
                + " &nbsp; <span style='color:#888'>┅ 최단 시간 경로</span>", unsafe_allow_html=True)


# ---------------- 오른쪽: 결과 ----------------
with right:
    if routes:
        sc, sf = routes["comfort"].stats(), routes["fast"].stats()
        same = np.array_equal(routes["comfort"].edges, routes["fast"].edges)
        st.subheader("③ 경로 비교")
        fmt = lambda v: f"{float(v):g}"
        table = pd.DataFrame({"😊 편한 경로": {k: fmt(v) for k, v in sc.items()},
                              "⚡ 최단 시간 경로": {k: fmt(v) for k, v in sf.items()}})
        st.dataframe(table, width="stretch")
        if same:
            st.caption("두 경로가 같아요. 이 구간은 가장 빠른 길이 곧 가장 편한 길이에요.")
        else:
            dt = sc["예상 시간 (분)"] - sf["예상 시간 (분)"]
            st.caption(f"편한 경로는 약 **{dt:+.0f}분** 차이로 큰길 비율을 {sf['큰길 비율 (%)']:.0f}% → "
                       f"{sc['큰길 비율 (%)']:.0f}%, 평균 편안함 점수를 {sf['평균 편안함 점수']} → {sc['평균 편안함 점수']}로 바꿔요.")
    else:
        st.info("👈 출발지와 도착지를 정해 주세요. 검색하거나, 지도를 클릭하거나, **✨ 예시** 버튼을 눌러 보세요.")

    tabs = st.tabs(["🗺️ 2D 지도", "🏔️ 2.5D 미리보기", "📈 경사 그래프", "🧭 길안내", "💾 내보내기"])

    # --- 2D 지도 (클릭으로 지점 지정) ---
    with tabs[0]:
        center = (ss.origin["lat"], ss.origin["lon"]) if ss.origin else (
            (ss.my_loc["lat"], ss.my_loc["lon"]) if ss.my_loc else SEOUL)
        m = folium.Map(location=center, zoom_start=13 if ss.origin else 11, tiles="OpenStreetMap")
        if routes:
            rf, rc = routes["fast"], routes["comfort"]
            folium.PolyLine(rf.coords()[:, ::-1].tolist(), color="#666", weight=4, dash_array="6,8",
                            opacity=0.8, tooltip="⚡ 최단 시간 경로").add_to(m)
            for colr, lab, seg in rc.colored_segments():
                folium.PolyLine(seg, color=colr, weight=6, opacity=0.95, tooltip=f"😊 편한 경로 · {lab}").add_to(m)
            xy = np.vstack([rc.coords(), rf.coords()])
            m.fit_bounds([[xy[:, 1].min(), xy[:, 0].min()], [xy[:, 1].max(), xy[:, 0].max()]])
        if ss.origin:
            folium.Marker([ss.origin["lat"], ss.origin["lon"]], tooltip="출발: " + ss.origin["label"],
                          icon=folium.Icon(color="green", icon="play")).add_to(m)
        if ss.dest:
            folium.Marker([ss.dest["lat"], ss.dest["lon"]], tooltip="도착: " + ss.dest["label"],
                          icon=folium.Icon(color="red", icon="flag")).add_to(m)
        for i, w in enumerate(ss.wps):
            folium.Marker([w["lat"], w["lon"]], tooltip=f"경유 {i + 1}: {w['label']}",
                          icon=folium.Icon(color="blue", icon="info-sign")).add_to(m)
        if ss.my_loc:
            folium.CircleMarker([ss.my_loc["lat"], ss.my_loc["lon"]], radius=8, color="#1e66f5",
                                fill=True, fill_opacity=0.9, tooltip="내 위치").add_to(m)
        out = st_folium(m, key="map2d", height=500, use_container_width=True, returned_objects=["last_clicked"])
        if routes:
            legend()
        lc = (out or {}).get("last_clicked")
        if lc:
            lc_t = (round(lc["lat"], 6), round(lc["lng"], 6))
            if lc_t != ss.last_click:
                ss.last_click = lc_t
                label = "지도에서 선택" if click_target != "경유지" else f"경유지 {len(ss.wps) + 1}"
                set_point(click_target, lc_t[0], lc_t[1], label)
                st.rerun()

    # --- 2.5D 미리보기 + 구간 넘기기 ---
    with tabs[1]:
        if not routes:
            st.info("경로를 먼저 찾아 주세요.")
        else:
            rc, rf = routes["comfort"], routes["fast"]
            steps = rc.steps()
            c1, c2 = st.columns([1, 1])
            ex = c1.slider("높이 과장 배율", 1, 8, 4, help="오르막·내리막을 잘 보이게 고도를 부풀려요")
            nav = c2.toggle("🧭 구간 넘기기 모드", help="회전 지점을 하나씩 넘기며 미리 따라가 봐요")
            zmin = float(G.Z[rc.nodes].min())
            layers = [
                pdk.Layer("PathLayer", route_3d(rf, ex, zmin, rgb=[120, 120, 120]), get_path="path",
                          get_color="color", width_min_pixels=2, get_width=2, opacity=0.5),
                pdk.Layer("PathLayer", route_3d(rc, ex, zmin), get_path="path", get_color="color",
                          width_min_pixels=5, get_width=6, pickable=True),
            ]
            marks = [{"lon": ss.origin["lon"], "lat": ss.origin["lat"], "c": [26, 152, 80], "label": "출발"},
                     {"lon": ss.dest["lon"], "lat": ss.dest["lat"], "c": [215, 48, 39], "label": "도착"}]
            marks += [{"lon": w["lon"], "lat": w["lat"], "c": [30, 102, 245], "label": f"경유 {i + 1}"}
                      for i, w in enumerate(ss.wps)]
            if ss.my_loc:
                marks.append({"lon": ss.my_loc["lon"], "lat": ss.my_loc["lat"], "c": [30, 102, 245], "label": "내 위치"})
            if nav:
                ss.step = max(0, min(ss.step, len(steps) - 1))
                n1, n2, n3 = st.columns(3)
                if n1.button("◀ 이전", width="stretch", disabled=ss.step == 0):
                    ss.step -= 1; st.rerun()
                if n2.button("⏮ 처음", width="stretch"):
                    ss.step = 0; st.rerun()
                if n3.button("다음 ▶", width="stretch", disabled=ss.step >= len(steps) - 1):
                    ss.step += 1; st.rerun()
                s = steps[ss.step]
                remain = sum(x["dist_m"] for x in steps[ss.step:]) / 1000
                road = f" · **{s['road']}**" if s["road"] else ""
                dist = f" 따라 {s['dist_m']:.0f}m" if s["dist_m"] else ""
                st.success(f"**{s['no']} / {len(steps)}** · {s['action']}{road}{dist}  \n남은 거리 약 {remain:.1f}km")
                marks.append({"lon": s["lon"], "lat": s["lat"], "c": [255, 200, 0], "label": "현재 구간"})
                vs = pdk.ViewState(latitude=s["lat"], longitude=s["lon"], zoom=17, pitch=60, bearing=s["bearing"])
            else:
                xy = np.vstack([rc.coords(), rf.coords()])
                span = max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1]) * 1.26, 0.005)
                zoom = float(np.log2(360 * 500 / (256 * span * 1.4)))
                vs = pdk.ViewState(latitude=float(xy[:, 1].mean()), longitude=float(xy[:, 0].mean()),
                                   zoom=min(zoom, 16), pitch=50, bearing=-15)
            layers.append(pdk.Layer("ScatterplotLayer", marks, get_position=["lon", "lat"], get_fill_color="c",
                                    get_radius=25, radius_min_pixels=6, pickable=True))
            deck = pdk.Deck(layers=layers, initial_view_state=vs, map_provider="carto", map_style="light",
                            tooltip={"text": "{label}"})
            st.pydeck_chart(deck, height=500)
            legend()
            st.caption("🚫 주행 중 조작 금지 · 이 화면은 출발 전 경로를 익히는 용도예요. "
                       "Ctrl(또는 두 손가락) + 드래그로 시점을 돌릴 수 있어요.")

    # --- 경사 그래프 ---
    with tabs[2]:
        if not routes:
            st.info("경로를 먼저 찾아 주세요.")
        else:
            pc = routes["comfort"].profile().assign(경로="😊 편한 경로")
            pf = routes["fast"].profile().assign(경로="⚡ 최단 시간 경로")
            df = pd.concat([pc, pf])
            chart = alt.Chart(df).mark_line(strokeWidth=2.5).encode(
                x=alt.X("거리 (km):Q"), y=alt.Y("고도 (m):Q", scale=alt.Scale(zero=False)),
                color=alt.Color("경로:N", scale=alt.Scale(range=["#1a9850", "#888888"]),
                                legend=alt.Legend(orient="top")),
                tooltip=["경로", alt.Tooltip("거리 (km):Q", format=".2f"), alt.Tooltip("고도 (m):Q", format=".0f")])
            st.altair_chart(chart, width="stretch")
            st.caption(f"누적 오르막: 편한 경로 {sc['누적 오르막 (m)']:.0f}m · 최단 시간 경로 {sf['누적 오르막 (m)']:.0f}m")

    # --- 길안내 ---
    with tabs[3]:
        if not routes:
            st.info("경로를 먼저 찾아 주세요.")
        else:
            steps = routes["comfort"].steps()
            sdf = pd.DataFrame([{"#": s["no"], "안내": s["action"], "도로": s["road"],
                                 "거리": f"{s['dist_m']:.0f}m" if s["dist_m"] else ""} for s in steps])
            st.dataframe(sdf, hide_index=True, width="stretch", height=360)
            sentences = [f"{s['no']}번. {s['action']}" + (f", {s['road']} 따라 {s['dist_m']:.0f}미터" if s["road"] and s["dist_m"] else "")
                         for s in steps]
            st.iframe(f"""
                <button id="b" style="width:100%;padding:10px;border-radius:8px;border:1px solid #ccc;
                        background:#f6f6f6;font-size:15px;cursor:pointer">🔊 출발 전 전체 안내 듣기</button>
                <script>
                const lines = {json.dumps(sentences, ensure_ascii=False)};
                document.getElementById('b').onclick = () => {{
                  speechSynthesis.cancel();
                  lines.forEach(t => {{ const u = new SpeechSynthesisUtterance(t); u.lang='ko-KR'; speechSynthesis.speak(u); }});
                }};
                </script>""", height=60)
            st.caption("🚫 주행 중 사용 금지 · 출발 전에 경로를 익히는 용도예요.")

    # --- 내보내기 ---
    with tabs[4]:
        if not routes:
            st.info("경로를 먼저 찾아 주세요.")
        else:
            e1, e2 = st.columns(2)
            e1.download_button("😊 편한 경로 GPX", routes["comfort"].gpx("편한 경로"), "bike_comfort_route.gpx",
                               "application/gpx+xml", width="stretch")
            e2.download_button("⚡ 최단 시간 경로 GPX", routes["fast"].gpx("최단 시간 경로"), "bike_fast_route.gpx",
                               "application/gpx+xml", width="stretch")
            st.markdown("GPX 파일을 **음성 안내를 지원하는 자전거 앱**에 불러오면, 이 경로를 따라 실제 길안내를 받을 수 있어요.")
            st.markdown("🔗 **즐겨찾기:** 지금 주소창의 링크를 북마크하거나 공유하면 같은 경로가 그대로 열려요.")

st.divider()
st.caption("데이터: OpenStreetMap 기여자, 서울시 고도 자료, 국가기본도 자전거도로 · 경로와 점수는 참고용이에요. "
           "🚫 주행 중 휴대폰 조작은 위험해요. 출발 전에 경로를 익혀 주세요.")
