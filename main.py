import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

st.set_page_config(page_title="박스오피스 대시보드", layout="wide")
st.title("🎬 박스오피스")

# 비밀 금고에서 인증키 꺼내기 (코드에는 키를 적지 않는다)
KOBIS_KEY = st.secrets["kobis_api_key"]
KOBIS_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"

THEMES = {
    "시네마": {
        "accent": "#7c2d12",
        "accent_soft": "rgba(124, 45, 18, 0.10)",
        "highlight": "rgba(194, 65, 12, 0.16)",
        "background": "#fffaf5",
    },
    "네온": {
        "accent": "#0f766e",
        "accent_soft": "rgba(15, 118, 110, 0.10)",
        "highlight": "rgba(20, 184, 166, 0.14)",
        "background": "#f4fffd",
    },
    "클래식": {
        "accent": "#1d4ed8",
        "accent_soft": "rgba(29, 78, 216, 0.10)",
        "highlight": "rgba(59, 130, 246, 0.14)",
        "background": "#f8fbff",
    },
}

# 한국 시간 기준 날짜 범위를 만든다. 오늘은 아직 집계 전이므로 어제까지만 고를 수 있다.
kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
yesterday = (kst_now - timedelta(days=1)).date()


def apply_theme(theme_name: str) -> None:
    theme = THEMES[theme_name]
    st.markdown(
        f"""
        <style>
        .stApp {{
            background: linear-gradient(180deg, {theme['background']} 0%, #ffffff 100%);
        }}
        h1, h2, h3 {{
            color: {theme['accent']};
        }}
        [data-testid="stMetricValue"] {{
            color: {theme['accent']};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=3600)
def fetch_daily_boxoffice(target_dt: str) -> tuple[pd.DataFrame | None, str | None, bool]:
    try:
        response = requests.get(
            KOBIS_URL,
            params={"key": KOBIS_KEY, "targetDt": target_dt},
            timeout=10,
        )
    except requests.RequestException as exc:
        return None, f"요청 중 오류가 발생했습니다: {exc}", False

    if response.status_code != 200:
        return None, f"요청이 실패했습니다 (상태코드: {response.status_code})", False

    data = response.json()
    if "faultInfo" in data:
        return None, "인증키가 올바르지 않습니다. 금고(Secrets)의 KOBIS_KEY를 확인해 주세요.", False

    box_list = data.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
    if not box_list:
        return pd.DataFrame(), None, True

    df = pd.DataFrame(box_list)
    for col in ["rank", "rankInten", "audiCnt", "audiAcc", "scrnCnt", "showCnt"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df, None, False


def movie_label(movie_nm: str, audi_acc: int) -> str:
    return movie_nm + (" 🏆" if audi_acc >= 1_000_000 else "")


def to_date_key(value: pd.Timestamp | datetime | str) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def date_range(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def aggregate_period_boxoffice(start_date, end_date, period_label: str) -> tuple[pd.DataFrame, bool]:
    daily_frames: list[pd.DataFrame] = []
    had_empty_day = False

    for current_date in date_range(start_date, end_date):
        daily_df, error_message, is_empty = fetch_daily_boxoffice(to_date_key(current_date))
        if error_message:
            raise RuntimeError(error_message)
        if is_empty or daily_df is None or daily_df.empty:
            had_empty_day = True
            continue
        temp_df = daily_df.copy()
        temp_df["조회일"] = current_date
        daily_frames.append(temp_df)

    if not daily_frames:
        return pd.DataFrame(), had_empty_day

    combined = pd.concat(daily_frames, ignore_index=True)
    combined = combined.sort_values(["movieNm", "조회일"]).reset_index(drop=True)

    grouped_rows = []
    for movie_nm, group in combined.groupby("movieNm", sort=False):
        latest = group.sort_values("조회일").iloc[-1]
        grouped_rows.append(
            {
                "rank": int(group["rank"].min()),
                "movieNm": movie_nm,
                "movieNmEn": latest.get("movieNmEn", ""),
                "openDt": latest.get("openDt", ""),
                "audiCnt": int(group["audiCnt"].sum()),
                "audiAcc": int(latest.get("audiAcc", 0)),
                "scrnCnt": int(group["scrnCnt"].max()),
                "showCnt": int(group["showCnt"].sum()),
                "rankInten": int(latest.get("rankInten", 0)),
                "periodDays": (end_date - start_date).days + 1,
                "periodLabel": period_label,
            }
        )

    period_df = pd.DataFrame(grouped_rows).sort_values(["audiCnt", "rank"], ascending=[False, True]).reset_index(drop=True)
    period_df["rank"] = range(1, len(period_df) + 1)
    return period_df, had_empty_day


def select_period(selected_date, mode: str) -> tuple[datetime.date, datetime.date, str]:
    if mode == "일간":
        return selected_date, selected_date, "일간"
    if mode == "주간":
        start_date = selected_date - timedelta(days=6)
        return start_date, selected_date, "주간"

    start_date = selected_date.replace(day=1)
    return start_date, selected_date, "월간"


def resolve_compare_period(start_date, mode: str) -> tuple[datetime.date, datetime.date, str]:
    if mode == "일간":
        compare_end = start_date - timedelta(days=1)
        return compare_end, compare_end, "전날"
    if mode == "주간":
        compare_end = start_date - timedelta(days=1)
        compare_start = compare_end - timedelta(days=6)
        return compare_start, compare_end, "전주"

    compare_end = start_date - timedelta(days=1)
    compare_start = compare_end.replace(day=1)
    return compare_start, compare_end, "전월"


def build_release_curve(movie_name: str, open_dt: str, end_date) -> pd.DataFrame:
    if not open_dt:
        return pd.DataFrame()

    start_date = pd.Timestamp(open_dt).date()
    curve_rows = []
    cumulative_audience = 0

    for current_date in date_range(start_date, end_date):
        daily_df, error_message, is_empty = fetch_daily_boxoffice(to_date_key(current_date))
        if error_message or is_empty or daily_df is None or daily_df.empty:
            daily_audience = 0
            rank_value = pd.NA
        else:
            match = daily_df[daily_df["movieNm"] == movie_name]
            if match.empty:
                daily_audience = 0
                rank_value = pd.NA
            else:
                daily_row = match.iloc[0]
                daily_audience = int(daily_row["audiCnt"])
                rank_value = int(daily_row["rank"])

        cumulative_audience += daily_audience
        curve_rows.append(
            {
                "날짜": current_date,
                "개봉 후 일수": (current_date - start_date).days + 1,
                "일간 관객수": daily_audience,
                "누적 관객": cumulative_audience,
                "순위": rank_value,
            }
        )

    return pd.DataFrame(curve_rows)


def format_rank_change(value) -> str:
    if pd.isna(value):
        return "신규"
    value = int(value)
    if value > 0:
        return f"🔴 ▲ {value}"
    if value < 0:
        return f"🔵 ▼ {abs(value)}"
    return "-"


def format_signed_count(value) -> str:
    if pd.isna(value):
        return "신규"
    value = int(value)
    return f"{value:+,}명"


def style_rank_change_cell(value: str) -> str:
    if value.startswith("🔴"):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits and int(digits) >= 3:
            return "color: #9f1239; font-weight: 900; background-color: rgba(244, 63, 94, 0.10);"
        return "color: #d32f2f; font-weight: 800;"
    if value.startswith("🔵"):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits and int(digits) >= 3:
            return "color: #1d4ed8; font-weight: 900; background-color: rgba(59, 130, 246, 0.10);"
        return "color: #1976d2; font-weight: 800;"
    if value == "신규":
        return "color: #6b7280; font-weight: 700;"
    return "font-weight: 700;"


def style_audience_change_cell(value: str) -> str:
    if value == "신규":
        return "color: #6b7280; font-weight: 700;"
    if value.startswith("+"):
        return "color: #15803d; font-weight: 700;"
    if value.startswith("-"):
        return "color: #b91c1c; font-weight: 700;"
    return "font-weight: 700;"


def style_table_rows(row: pd.Series, selected_movie: str | None, theme_name: str) -> list[str]:
    theme = THEMES[theme_name]
    base_color = ""
    if row["누적관객"] >= 5_000_000:
        base_color = "background-color: rgba(245, 158, 11, 0.16);"
    elif row["누적관객"] >= 3_000_000:
        base_color = "background-color: rgba(16, 185, 129, 0.12);"
    elif row["누적관객"] >= 1_000_000:
        base_color = "background-color: rgba(59, 130, 246, 0.10);"

    if selected_movie and str(row["영화명"]).startswith(selected_movie):
        base_color = f"background-color: {theme['highlight']}; border-left: 4px solid {theme['accent']};"

    rank_digits = "".join(ch for ch in str(row.get("순위 증감", "")) if ch.isdigit())
    if rank_digits and int(rank_digits) >= 3:
        base_color += "font-weight: 700;"

    return [base_color] * len(row)


kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
yesterday = (kst_now - timedelta(days=1)).date()

with st.sidebar:
    st.header("조회 설정")
    theme_name = st.selectbox("화면 테마", list(THEMES.keys()))
    apply_theme(theme_name)

    mode = st.selectbox("집계 모드", ["일간", "주간", "월간"])
    selected_date = st.date_input(
        "조회 날짜",
        value=yesterday,
        max_value=yesterday,
    )
    st.subheader("히트작 강조")
    hit_100 = st.checkbox("100만 이상", value=True)
    hit_300 = st.checkbox("300만 이상")
    hit_500 = st.checkbox("500만 이상")
    hide_lower_rank = st.checkbox("선택 기준만 보기")

start_date, end_date, period_label = select_period(selected_date, mode)
compare_start, compare_end, compare_label = resolve_compare_period(start_date, mode)

if start_date > yesterday:
    st.error("오늘은 아직 집계 전입니다. 어제까지의 날짜만 선택해 주세요.")
    st.stop()

st.caption(
    f"조회 기준일: {selected_date:%Y-%m-%d} / 집계 구간: {start_date:%Y-%m-%d} ~ {end_date:%Y-%m-%d} ({period_label})"
)

try:
    if mode == "일간":
        selected_df, error_message, selected_empty = fetch_daily_boxoffice(to_date_key(selected_date))
    else:
        selected_df, selected_empty = aggregate_period_boxoffice(start_date, end_date, period_label)
        error_message = None
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()

if error_message:
    st.error(error_message)
    st.stop()

if selected_empty or selected_df is None or selected_df.empty:
    st.warning("그날은 아직 집계 전입니다")
    st.stop()

try:
    if mode == "일간":
        compare_df, compare_error, compare_empty = fetch_daily_boxoffice(to_date_key(compare_end))
    else:
        compare_df, compare_empty = aggregate_period_boxoffice(compare_start, compare_end, compare_label)
        compare_error = None
except RuntimeError as exc:
    compare_df = pd.DataFrame()
    compare_error = str(exc)

if compare_error:
    st.warning(f"비교 기준일 조회에 실패했습니다: {compare_error}")
    compare_df = pd.DataFrame()

selected_df = selected_df.sort_values("rank").reset_index(drop=True)
selected_df["영화명"] = selected_df.apply(lambda row: movie_label(row["movieNm"], row["audiAcc"]), axis=1)

if compare_df is not None and not compare_df.empty:
    compare_lookup = compare_df.set_index("movieNm")
    selected_df["비교 순위"] = selected_df["movieNm"].map(compare_lookup["rank"])
    selected_df["비교 관객수"] = selected_df["movieNm"].map(compare_lookup["audiCnt"])
    selected_df["순위 변화값"] = selected_df["비교 순위"] - selected_df["rank"]
    selected_df["관객 변화값"] = selected_df["audiCnt"] - selected_df["비교 관객수"]
    selected_df["관객 변화율"] = selected_df["관객 변화값"] / selected_df["비교 관객수"].replace(0, pd.NA) * 100
else:
    selected_df["비교 순위"] = pd.NA
    selected_df["비교 관객수"] = pd.NA
    selected_df["순위 변화값"] = pd.NA
    selected_df["관객 변화값"] = pd.NA
    selected_df["관객 변화율"] = pd.NA

selected_df["상영횟수"] = selected_df["showCnt"]
display_df = selected_df.copy()

thresholds = []
if hit_100:
    thresholds.append(1_000_000)
if hit_300:
    thresholds.append(3_000_000)
if hit_500:
    thresholds.append(5_000_000)

detail_df = selected_df.copy()
view_df = display_df.copy()
if thresholds and hide_lower_rank:
    cutoff = max(thresholds)
    view_df = view_df[view_df["audiAcc"] >= cutoff].copy()

top = selected_df.iloc[0]
selected_total = int(selected_df["audiCnt"].sum())
compare_total = int(compare_df["audiCnt"].sum()) if compare_df is not None and not compare_df.empty else None

selected_movie_default = detail_df.sort_values("rank").iloc[0]["movieNm"]
selected_movie = st.session_state.get("selected_movie", selected_movie_default)

c1, c2, c3 = st.columns(3)
c1.metric("1위 영화", top["영화명"])
c2.metric(
    f"{period_label} 총 관객수",
    f"{selected_total:,}명",
    delta=(f"{selected_total - compare_total:+,}명" if compare_total is not None else None),
)
c3.metric("표시 영화 수", f"{len(view_df):,}편")

comparison_rows = selected_df[selected_df["순위 변화값"].notna()].copy()
if not comparison_rows.empty:
    best_up = comparison_rows.sort_values("순위 변화값", ascending=False).iloc[0]
    best_drop = comparison_rows.sort_values("순위 변화값", ascending=True).iloc[0]
    best_gain = comparison_rows.sort_values("관객 변화값", ascending=False).iloc[0]
    st.info(
        f"{compare_label} 대비 가장 많이 오른 영화는 {movie_label(best_up['movieNm'], best_up['audiAcc'])}({int(best_up['순위 변화값'])}칸), "
        f"가장 많이 늘어난 관객은 {movie_label(best_gain['movieNm'], best_gain['audiAcc'])}(+{int(best_gain['관객 변화값']):,}명)입니다. "
        f"가장 많이 내린 영화는 {movie_label(best_drop['movieNm'], best_drop['audiAcc'])}({int(best_drop['순위 변화값'])}칸)입니다."
    )

table_columns = ["순위", "영화명", "개봉일", "관객수", "누적관객", "스크린수", "상영횟수", "순위 증감", "관객 변화"]
table_df = view_df[
    ["rank", "영화명", "openDt", "audiCnt", "audiAcc", "scrnCnt", "showCnt", "순위 변화값", "관객 변화값"]
].copy()
table_df.columns = table_columns
table_df["순위 증감"] = table_df["순위 증감"].apply(format_rank_change)
table_df["관객 변화"] = table_df["관객 변화"].apply(format_signed_count)

styled_table = (
    table_df.style
    .apply(lambda row: style_table_rows(row, selected_movie, theme_name), axis=1)
    .applymap(style_rank_change_cell, subset=["순위 증감"])
    .applymap(style_audience_change_cell, subset=["관객 변화"])
    .format({"관객수": "{:,.0f}", "누적관객": "{:,.0f}", "스크린수": "{:,.0f}", "상영횟수": "{:,.0f}"})
)

if thresholds and not hide_lower_rank:
    st.caption("히트작 기준은 선택된 영화들을 강조하는 데 사용됩니다. 필요하면 오른쪽 체크박스로 목록을 좁힐 수 있습니다.")

if thresholds and hide_lower_rank and view_df.empty:
    st.warning("선택한 기준에 맞는 영화가 없습니다. 체크박스를 줄이거나 해제해 보세요.")

left_col, right_col = st.columns([2, 1], gap="large")

with left_col:
    st.subheader("📋 박스오피스 TOP 10")
    st.dataframe(styled_table, use_container_width=True, hide_index=True)

    st.subheader("📈 관객수 상위 5편")
    chart_df = view_df.sort_values("audiCnt", ascending=False).head(5)
    if not chart_df.empty:
        st.bar_chart(chart_df.set_index("영화명")["audiCnt"])
    else:
        st.info("차트에 표시할 영화가 없습니다.")

with right_col:
    st.subheader("🎞 영화 상세")
    detail_options = detail_df.sort_values("rank")["movieNm"].tolist()
    selected_movie = st.selectbox("영화 선택", detail_options, index=detail_options.index(selected_movie) if selected_movie in detail_options else 0, key="selected_movie")
    detail_row = detail_df[detail_df["movieNm"] == selected_movie].iloc[0]

    st.markdown(f"### {movie_label(detail_row['movieNm'], detail_row['audiAcc'])}")

    d1, d2, d3 = st.columns(3)
    d1.metric("순위", f"{int(detail_row['rank'])}위")
    d2.metric("관객수", f"{int(detail_row['audiCnt']):,}명")
    d3.metric("누적관객", f"{int(detail_row['audiAcc']):,}명")

    d4, d5, d6 = st.columns(3)
    d4.metric("스크린수", f"{int(detail_row['scrnCnt']):,}개")
    d5.metric("상영횟수", f"{int(detail_row['showCnt']):,}회")
    d6.metric("개봉일", detail_row["openDt"])

    st.write("순위 변화:", format_rank_change(detail_row["순위 변화값"]))
    st.write("관객 변화:", format_signed_count(detail_row["관객 변화값"]))

    if pd.notna(detail_row["관객 변화율"]):
        st.caption(f"비교 기준 대비 관객 변화율: {detail_row['관객 변화율']:+.1f}%")

    st.caption("목록에서 고른 영화의 핵심 지표와 비교 기준 대비 변화가 함께 표시됩니다.")

    st.subheader("📈 개봉 후 흥행 곡선")
    release_curve = build_release_curve(detail_row["movieNm"], detail_row["openDt"], end_date)
    if release_curve.empty:
        st.info("개봉 후 곡선을 만들 수 있는 데이터가 없습니다.")
    else:
        curve_view = release_curve.set_index("개봉 후 일수")[["누적 관객", "일간 관객수"]]
        st.line_chart(curve_view[["누적 관객"]])
        st.bar_chart(curve_view[["일간 관객수"]])