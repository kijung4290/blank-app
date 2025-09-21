import os, io, json, base64
from typing import List, Dict
import streamlit as st
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import numpy as np
import fitz  # PyMuPDF

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account

# ----------------------------
# 설정
# ----------------------------
st.set_page_config(page_title="PDF 다중 서명 · Drive 업로드", layout="wide")

DEFAULT_ZOOM = 2.0  # 미리보기 렌더 배율 (px = pt * zoom)
OUTPUT_PDF_NAME = "form_stamped.pdf"

# 기본 필드 좌표 (pt, 원점=좌하단). page는 1부터.
FIELDS: List[Dict] = [
    {"key": "name",   "label": "이름",     "page": 1, "x": 482, "y": 479, "w": 59,  "h": 41},
    {"key": "phone",  "label": "전화번호", "page": 1, "x": 120, "y": 630, "w": 260, "h": 40},
    {"key": "birth",  "label": "생년월일", "page": 1, "x": 120, "y": 580, "w": 260, "h": 40},
    {"key": "userid", "label": "아이디",   "page": 1, "x": 120, "y": 530, "w": 220, "h": 40},
    {"key": "consent","label": "동의",     "page": 1, "x": 120, "y": 120, "w": 120, "h": 40},
    {"key": "sign",   "label": "서명",     "page": 1, "x": 380, "y": 360, "w": 220, "h": 60},
]

# ----------------------------
# 유틸
# ----------------------------
def load_pdf(stream_or_path) -> fitz.Document:
    if isinstance(stream_or_path, (bytes, bytearray)):
        return fitz.open(stream=stream_or_path, filetype="pdf")
    if hasattr(stream_or_path, "read"):  # UploadedFile
        return fitz.open(stream=stream_or_path.read(), filetype="pdf")
    return fitz.open(stream_or_path, filetype="pdf")

def render_all_pages_b64(doc: fitz.Document, zoom: float = DEFAULT_ZOOM):
    pages = []
    for i, page in enumerate(doc, start=1):
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode("ascii")
        pages.append({
            "page": i,
            "b64": b64,
            "img_w": pix.width,
            "img_h": pix.height,
            "w_pt": page.rect.width,
            "h_pt": page.rect.height,
            "zoom": zoom,
        })
    return pages

def stamp_multiple_into_pdf(original_pdf_bytes: bytes, values: Dict[str, bytes], fields: List[Dict]) -> bytes:
    """values: {key: PNG bytes}; fields 좌표에 모두 스탬프해 PDF bytes 반환"""
    doc = fitz.open(stream=original_pdf_bytes, filetype="pdf")
    for f in fields:
        key = f["key"]
        if key not in values:
            continue
        img_bytes = values[key]
        page = doc[f["page"] - 1]
        x, y, w, h = f["x"], f["y"], f["w"], f["h"]
        rect = fitz.Rect(x, y, x + w, y + h)
        page.insert_image(rect, stream=img_bytes, keep_proportion=False)
    out = doc.write()
    doc.close()
    return out

def get_drive_service_from_env():
    sa_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not sa_json:
        return None, "환경변수 GOOGLE_CREDENTIALS_JSON 가 설정되지 않았습니다."
    try:
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        svc = build("drive", "v3", credentials=creds)
        return svc, None
    except Exception as e:
        return None, f"서비스 계정 로딩 실패: {e}"

def upload_to_drive(pdf_bytes: bytes, filename: str, folder_id: str = ""):
    svc, err = get_drive_service_from_env()
    if err:
        return None, err
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False)
    meta = {"name": filename}
    if folder_id:
        meta["parents"] = [folder_id]
    try:
        res = svc.files().create(
            body=meta, media_body=media,
            fields="id, name, parents, webViewLink, webContentLink"
        ).execute()
        return res, None
    except Exception as e:
        return None, f"Drive 업로드 실패: {e}"

def rgba_numpy_to_png_bytes(arr: np.ndarray) -> bytes:
    """streamlit-drawable-canvas의 RGBA numpy를 PNG로"""
    img = Image.fromarray(arr.astype("uint8"), mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def b64_to_pil(b64str: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64str)))

# ----------------------------
# 상태
# ----------------------------
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "pages" not in st.session_state:
    st.session_state.pages = []   # [{page, b64, img_w, img_h, w_pt, h_pt, zoom}]
if "values_png" not in st.session_state:
    st.session_state.values_png = {}  # {key: PNG bytes}
if "previews" not in st.session_state:
    st.session_state.previews = {}    # {page: PIL.Image (composited)}
if "zoom" not in st.session_state:
    st.session_state.zoom = DEFAULT_ZOOM

# ----------------------------
# 사이드바: 입력
# ----------------------------
st.sidebar.header("입력 / 설정")
uploaded = st.sidebar.file_uploader("PDF 업로드 (선택). 없으면 form.pdf 사용", type=["pdf"])
zoom = st.sidebar.slider("미리보기 배율 (zoom)", 1.0, 3.0, st.session_state.zoom, 0.1)
st.session_state.zoom = zoom

folder_id = st.sidebar.text_input("Google Drive 폴더 ID (선택)", os.environ.get("DRIVE_FOLDER_ID", ""))
st.sidebar.caption("폴더를 서비스 계정 이메일에 '편집 권한'으로 공유해야 업로드 가능.")

use_repo_pdf = st.sidebar.checkbox("리포지토리의 form.pdf 사용", value=True if not uploaded else False)

if st.sidebar.button("PDF 불러오기 / 초기화", use_container_width=True):
    try:
        if uploaded and not use_repo_pdf:
            pdf_bytes = uploaded.read()
        else:
            with open("form.pdf", "rb") as f:
                pdf_bytes = f.read()
        doc = load_pdf(pdf_bytes)
        st.session_state.pdf_bytes = pdf_bytes
        st.session_state.pages = render_all_pages_b64(doc, zoom=st.session_state.zoom)
        st.session_state.values_png = {}
        st.session_state.previews = {}
        st.success("PDF 로드 완료")
    except Exception as e:
        st.error(f"PDF 로드 실패: {e}")

st.title("PDF 다중 서명 · Google Drive 업로드 (Streamlit)")

if not st.session_state.pages:
    st.info("좌측에서 PDF를 불러오세요. (업로드 또는 form.pdf)")
    st.stop()

# ----------------------------
# 페이지별 미리보기 및 필드별 서명 캔버스
# ----------------------------
pages = st.session_state.pages
values_png = st.session_state.values_png

# 미리보기 컴포지트 준비 (페이지별)
def get_page_composite(pinfo):
    """페이지 원본 PNG + 각 필드 서명 PNG를 화면 px 기준으로 합성"""
    page_img = b64_to_pil(pinfo["b64"]).convert("RGBA")
    # 필드별 합성
    for f in FIELDS:
        if f["page"] != pinfo["page"]:
            continue
        key = f["key"]
        if key not in values_png:
            continue
        # 화면 px 좌표
        scale = pinfo["zoom"]
        dom_x = int(f["x"] * scale)
        dom_y = int((pinfo["h_pt"] - f["y"] - f["h"]) * scale)  # 좌하단→좌상단
        dom_w = int(f["w"] * scale)
        dom_h = int(f["h"] * scale)
        # 서명 PNG
        sign_img = Image.open(io.BytesIO(values_png[key])).convert("RGBA")
        sign_img = sign_img.resize((dom_w, dom_h), Image.LANCZOS)
        page_img.alpha_composite(sign_img, (dom_x, dom_y))
    return page_img

# 레이아웃: 좌측 미리보기, 우측 필드별 캔버스
left, right = st.columns([3, 2], gap="large")

with left:
    st.subheader("미리보기 (서명 적용 즉시 반영)")
    for p in pages:
        comp = get_page_composite(p)
        st.image(comp, caption=f"페이지 {p['page']} (zoom={p['zoom']})", use_container_width=False)

with right:
    st.subheader("필드별 서명")
    # 필드를 페이지 순으로 묶어서 표시
    for page_no in sorted(set(f["page"] for f in FIELDS)):
        st.markdown(f"### 페이지 {page_no}")
        page_info = next(pp for pp in pages if pp["page"] == page_no)
        scale = page_info["zoom"]
        for f in [x for x in FIELDS if x["page"] == page_no]:
            st.caption(f"• {f['label']} (key={f['key']})  [ {f['w']}×{f['h']} pt ]")
            dom_w = int(f["w"] * scale)
            dom_h = int(f["h"] * scale)

            # 캔버스: 빈 투명 배경 (원하면 해당 영역 크롭을 배경으로 줄 수도 있음)
            canvas = st_canvas(
                fill_color="rgba(0,0,0,0)",
                stroke_width=2,
                stroke_color="#000000",
                background_color="rgba(0,0,0,0)",
                height=max(40, dom_h),
                width=max(120, dom_w),
                drawing_mode="freedraw",
                key=f"canvas_{f['key']}",
            )

            c1, c2, c3 = st.columns([1,1,2])
            if c1.button("적용", key=f"apply_{f['key']}"):
                if canvas.image_data is None:
                    st.warning("먼저 서명을 그려주세요.")
                else:
                    png_bytes = rgba_numpy_to_png_bytes(canvas.image_data)
                    values_png[f["key"]] = png_bytes
                    st.session_state.values_png = values_png  # 저장
                    st.success("적용 완료 → 좌측 미리보기에 반영되었습니다.")

            if c2.button("지우기", key=f"clear_{f['key']}"):
                if f["key"] in values_png:
                    del values_png[f["key"]]
                    st.session_state.values_png = values_png
                st.experimental_rerun()

    st.divider()
    st.markdown("#### 최종 처리")
    do_download = st.checkbox("업로드 전, PDF 결과를 미리 다운로드")
    folder_id_input = st.text_input("Drive 폴더 ID (미입력 시 My Drive)", value=folder_id)

    if st.button("Google Drive로 업로드", type="primary", use_container_width=True):
        if not st.session_state.pdf_bytes:
            st.error("PDF가 로드되어 있지 않습니다.")
        elif not st.session_state.values_png:
            st.error("적용된 서명이 없습니다.")
        else:
            # PDF 합성
            try:
                stamped_pdf = stamp_multiple_into_pdf(
                    original_pdf_bytes=st.session_state.pdf_bytes,
                    values=st.session_state.values_png,
                    fields=FIELDS,
                )
            except Exception as e:
                st.error(f"PDF 합성 실패: {e}")
                st.stop()

            # (선택) 다운로드
            if do_download:
                st.download_button(
                    "PDF 다운로드",
                    data=stamped_pdf,
                    file_name=OUTPUT_PDF_NAME,
                    mime="application/pdf",
                )

            # Drive 업로드
            res, err = upload_to_drive(stamped_pdf, OUTPUT_PDF_NAME, folder_id_input.strip())
            if err:
                st.error(err)
            else:
                st.success(f"업로드 완료 · fileId: {res.get('id')}")
                if res.get("webViewLink"):
                    st.markdown(f"[웹에서 열기]({res['webViewLink']})")

st.caption("팁: 좌측 미리보기는 각 필드의 (x,y,w,h)을 화면 배율에 맞춰 합성합니다. 실제 PDF 스탬프는 pt 좌표 그대로 정확히 들어갑니다.")
