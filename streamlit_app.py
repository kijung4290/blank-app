import streamlit as st
import pandas as pd
import plotly as px

# ----------------- 페이지 설정 -----------------
st.set_page_config(
    page_title="행복2팀 실적 대시보드",
    page_icon="📊",
    layout="wide",
)

# ----------------- 데이터 로딩 및 전처리 -----------------
# 데이터 로딩 함수 (캐싱을 통해 성능 최적화)
@st.cache_data
def load_data(uploaded_file):
    try:
        df = pd.read_csv(uploaded_file, encoding='utf-8')
    except UnicodeDecodeError:
        df = pd.read_csv(uploaded_file, encoding='cp949')
    
    # 불필요한 열 제거
    df = df.dropna(axis=1, how='all')
    
    # 데이터 타입 변환 및 정리
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    df = df.dropna(subset=['날짜']) # 날짜가 없는 행 제거
    df['월'] = df['날짜'].dt.month
    
    # 결측치 처리
    df['담당자'] = df['담당자'].fillna('미지정')
    df['보호구분'] = df['보호구분'].fillna('정보없음')
    df['상담유형'] = df['상담유형'].fillna('정보없음')

    return df

# ----------------- 사이드바 -----------------
st.sidebar.header("📂 파일 업로드")
uploaded_file = st.sidebar.file_uploader("CSV 파일을 선택하세요.", type=["csv"])

# 파일이 업로드 되면 대시보드 실행
if uploaded_file is not None:
    df = load_data(uploaded_file)

    st.sidebar.header("🔎 필터를 적용하세요")
    # 담당자 선택
    selected_담당자 = st.sidebar.multiselect(
        '담당자 선택',
        options=df['담당자'].unique(),
        default=df['담당자'].unique()
    )
    # 보호구분 선택
    selected_보호구분 = st.sidebar.multiselect(
        '보호구분 선택',
        options=df['보호구분'].unique(),
        default=df['보호구분'].unique()
    )
    # 상담유형 선택
    selected_상담유형 = st.sidebar.multiselect(
        '상담유형 선택',
        options=df['상담유형'].unique(),
        default=df['상담유형'].unique()
    )
    # 날짜 범위 선택
    min_date = df['날짜'].min().date()
    max_date = df['날짜'].max().date()
    selected_start_date = st.sidebar.date_input('시작 날짜', min_date)
    selected_end_date = st.sidebar.date_input('종료 날짜', max_date)

    # 선택된 값으로 데이터 필터링
    filtered_df = df[
        (df['담당자'].isin(selected_담당자)) &
        (df['보호구분'].isin(selected_보호구분)) &
        (df['상담유형'].isin(selected_상담유형)) &
        (df['날짜'].dt.date >= selected_start_date) &
        (df['날짜'].dt.date <= selected_end_date)
    ]

    # ----------------- 대시보드 메인 화면 -----------------
    st.title("📊 행복2팀 실적 현황 대시보드")
    st.markdown("---")

    # KPI 카드
    total_cases = len(df)
    filtered_cases = len(filtered_df)
    담당자_수 = len(filtered_df['담당자'].unique())

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(label="전체 업무 건수", value=f"{total_cases} 건")
    with col2:
        st.metric(label="필터링된 업무 건수", value=f"{filtered_cases} 건")
    with col3:
        st.metric(label="선택된 담당자 수", value=f"{담당자_수} 명")

    st.markdown("---")

    # 시각화 (2열 레이아웃)
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("담당자별 업무 처리 건수")
        if not filtered_df.empty:
            담당자_counts = filtered_df['담당자'].value_counts()
            fig_bar = px.bar(
                x=담당자_counts.index, y=담당자_counts.values,
                labels={'x': '담당자', 'y': '업무 건수'}, text=담당자_counts.values, color=담당자_counts.index
            )
            fig_bar.update_traces(textposition='outside')
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.warning("선택된 조건에 해당하는 데이터가 없습니다.")

    with col2:
        st.subheader("보호구분별 분포")
        if not filtered_df.empty:
            보호구분_counts = filtered_df['보호구분'].value_counts()
            fig_pie = px.pie(
                values=보호구분_counts.values, names=보호구분_counts.index, hole=0.4
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.warning("선택된 조건에 해당하는 데이터가 없습니다.")

    st.subheader("월별 업무량 추이")
    if not filtered_df.empty:
        monthly_counts = filtered_df['월'].value_counts().sort_index()
        fig_line = px.line(
            x=monthly_counts.index, y=monthly_counts.values,
            labels={'x': '월', 'y': '업무 건수'}, markers=True
        )
        st.plotly_chart(fig_line, use_container_width=True)
    else:
        st.warning("선택된 조건에 해당하는 데이터가 없습니다.")

    st.subheader("상세 데이터 보기")
    st.dataframe(filtered_df, use_container_width=True)

else:
    st.info("사이드바에서 CSV 파일을 업로드해주세요.")
