import os, io, json, base64
from typing import List, Dict
from pathlib import Path

import streamlit as st
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import numpy as np
import fitz  # PyMuPDF

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account

# ----------------------------
# 기본 설정
# ----------------------------
st.set_page_config(page_title="PDF 필드별 서명 · Drive 업로드 (B 방식)", layout="wide")

DEFAULT_ZOOM = 2.0                      # 미리보기 배율(px = pt * zoom)
OUTPUT_PDF_NAME = "form_stamped.pdf"    # 업로드 파일명 기본값

# 필드 좌표 (pt, 원점=좌하단) / page는 1부터 시작
FIELDS: List[Dict] = [
    {"key":"name",   "label":"이름",     "page":1, "x":482, "y":479, "w":59,  "h":41},
    {"key":"phone",  "label":"전화번호", "page":1, "x":120, "y":630, "w":260, "h":40},
    {"key":"birth",  "label":"생년월일", "page":1, "x":120, "y":580, "w":260, "h":40},
    {"key":"userid", "label":"아이디",   "page":1, "x":120, "y":530, "w":220, "h":40},
    {"key":"consent","label":"동의",     "page":1, "x":120, "y":120, "w":120, "h":40},
    {"key":"sign",   "label":"서명",     "page":1, "x":380, "y":360, "w":220, "h":60},
]

# ----------------------------
# 유틸
# ----------------------------
def load_pdf_bytes_from_repo() -> bytes:
    return Path("form.pdf").read_bytes()

def load_pdf_bytes_from_upload(uploaded) -> bytes:
    return uploaded.read()

def load_pdf_bytes_from_drive(file_id: str) -> bytes:
    """서비스 계정으로 Drive에서 파일 바이트 다운로드"""
    sa_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not sa_json:
        raise RuntimeError("환경변수 GOOGLE_CREDENTIALS_JSON이 필요합니다.")
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    svc = build("drive", "v3", credentials=creds)
    # media download
    req = svc.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = None
    # 간단히 execute()로 전체 바이트:
    data = req.execute()
    return data

def render_all_pages_b64(pdf_bytes: bytes, zoom: float = DEFAULT_ZOOM):
    """PDF 전 페이지를 렌더 → [ {page, b64, img_w, img_h, w_pt, h_pt, zoom} ]"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
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
            "w_pt": float(page.rect.width),
            "h_pt": float(page.rect.height),
            "zoom": float(zoom),
        })
    doc.close()
    return pages

def rgba_numpy_to_png_bytes(arr: np.ndarray) -> bytes:
    img = Image.fromarray(arr.astype("uint8"), mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def b64_to_pil(b64str: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64str)))

def stamp_multiple_into_pdf(original_pdf_bytes: bytes, values_png: Dict[str, bytes], fields: List[Dict]) -> bytes:
    """values_png: {key: PNG bytes}를 fields 좌표에 모두 삽입 → PDF bytes 반환"""
    doc = fitz.open(stream=original_pdf_bytes, filetype="pdf")
    for f in fields:
        key = f["key"]
        if key not in values_png:
            continue
        page = doc[f["page"] - 1]
        rect = fitz.Rect(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])
        page.insert_image(rect, stream=values_png[key], keep_proportion=False)
    out = doc.write()
    doc.close()
    return out

def get_drive_service() -> "googleapiclient.discovery.Resource":
    sa_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not sa_json:
        raise RuntimeError("환경변수 GOOGLE_CREDENTIALS_JSON이 필요합니다.")
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build("drive", "v3", credentials=creds)

def upload_to_drive(pdf_bytes: bytes, filename: str, folder_id: str = ""):
    svc = get_drive_service()
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False)
    meta = {"name": filename}
    if folder_id.strip():
        meta["parents"] = [folder_id.strip()]
    return svc.files().create(
        body=meta, media_body=media,
        fields="id, name, parents, webViewLink, webContentLink"
    ).execute()

# ----------------------------
# 상태
# ----------------------------
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "pages" not in st.session_state:
    st.session_state.pages = []   # [{...}]
if "values_png" not in st.session_state:
    st.session_state.values_png = {}  # {key: PNG bytes}
if "zoom" not in st.session_state:
    st.session_state.zoom = DEFAULT_ZOOM

# ----------------------------
# 사이드바: 소스 선택 / 설정
# ----------------------------
st.sidebar.header("PDF 소스 선택")
src = st.sidebar.radio("선택", ["리포지토리 form.pdf", "업로드", "Drive File ID"], index=0)
uploaded = None
drive_file_id = ""
if src == "업로드":
    uploaded = st.sidebar.file_uploader("PDF 업로드", type=["pdf"])
elif src == "Drive File ID":
    drive_file_id = st.sidebar.text_input("Drive File ID", value="", placeholder="예: 1AbCDef...")

zoom = st.sidebar.slider("미리보기 배율 (zoom)", 1.0, 3.0, st.session_state.zoom, 0.1)
st.session_state.zoom = zoom

folder_id_env = os.environ.get("DRIVE_FOLDER_ID", "")
folder_id_input = st.sidebar.text_input("업로드 폴더 ID (선택)", value=folder_id_env)
st.sidebar.caption("※ 해당 폴더는 서비스 계정 이메일에 '편집 권한'으로 공유되어야 합니다.")

if st.sidebar.button("PDF 불러오기 / 초기화", use_container_width=True):
    try:
        if src == "리포지토리 form.pdf":
            pdf_bytes = load_pdf_bytes_from_repo()
        elif src == "업로드":
            if not uploaded:
                st.sidebar.error("파일을 업로드하세요.")
                st.stop()
            pdf_bytes = load_pdf_bytes_from_upload(uploaded)
        else:
            if not drive_file_id.strip():
                st.sidebar.error("Drive File ID를 입력하세요.")
                st.stop()
            pdf_bytes = load_pdf_bytes_from_drive(drive_file_id.strip())

        st.session_state.pdf_bytes = pdf_bytes
        st.session_state.pages = render_all_pages_b64(pdf_bytes, zoom=zoom)
        st.session_state.values_png = {}
        st.success("PDF 로드 완료")
    except Exception as e:
        st.sidebar.error(f"PDF 로드 실패: {e}")

st.title("PDF 필드별 서명 · Google Drive 업로드 (B 방식)")

if not st.session_state.pages:
    st.info("좌측에서 PDF를 불러오세요. (리포지토리 form.pdf / 업로드 / Drive File ID)")
    st.stop()

pages = st.session_state.pages
values_png = st.session_state.values_png

# ----------------------------
# 미리보기 합성: 필드별 적용을 페이지 이미지에 즉시 반영
# ----------------------------
def composited_page_image(pinfo):
    base = b64_to_pil(pinfo["b64"]).convert("RGBA")
    scale = pinfo["zoom"]
    for f in FIELDS:
        if f["page"] != pinfo["page"]:
            continue
        key = f["key"]
        if key not in values_png:
            continue
        # 화면 좌표로 변환
        dom_x = int(f["x"] * scale)
        dom_y = int((pinfo["h_pt"] - f["y"] - f["h"]) * scale)
        dom_w = int(f["w"] * scale)
        dom_h = int(f["h"] * scale)
        sign = Image.open(io.BytesIO(values_png[key])).convert("RGBA").resize((dom_w, dom_h), Image.LANCZOS)
        base.alpha_composite(sign, (dom_x, dom_y))
    return base

left, right = st.columns([3, 2], gap="large")

with left:
    st.subheader("미리보기 (적용 즉시 반영)")
    for p in pages:
        img = composited_page_image(p)
        st.image(img, caption=f"페이지 {p['page']} (zoom={p['zoom']})")

with right:
    st.subheader("필드별 서명")
    # 페이지 순으로 정렬하여 필드 그룹 표시
    for page_no in sorted(set(f["page"] for f in FIELDS)):
        st.markdown(f"### 페이지 {page_no}")
        pinfo = next(pp for pp in pages if pp["page"] == page_no)
        scale = pinfo["zoom"]
        for f in [x for x in FIELDS if x["page"] == page_no]:
            st.caption(f"• {f['label']} (key={f['key']})  [ {f['w']}×{f['h']} pt ]")
            dom_w = max(120, int(f["w"] * scale))
            dom_h = max(40,  int(f["h"] * scale))

            # 투명 배경의 전용 캔버스
            canvas = st_canvas(
                fill_color="rgba(0,0,0,0)",
                stroke_width=2,
                stroke_color="#000000",
                background_color="rgba(0,0,0,0)",
                height=dom_h,
                width=dom_w,
                drawing_mode="freedraw",
                key=f"canvas_{f['key']}",
            )

            c1, c2, c3 = st.columns([1,1,2])
            if c1.button("적용", key=f"apply_{f['key']}"):
                if canvas.image_data is None:
                    st.warning("먼저 서명을 그려주세요.")
                else:
                    bytes_png = rgba_numpy_to_png_bytes(canvas.image_data)
                    values_png[f["key"]] = bytes_png
                    st.session_state.values_png = values_png
                    st.success("적용 완료 → 좌측 미리보기에 반영")

            if c2.button("지우기", key=f"clear_{f['key']}"):
                if f["key"] in values_png:
                    del values_png[f["key"]]
                    st.session_state.values_png = values_png
                st.experimental_rerun()

    st.divider()
    st.markdown("#### 최종 처리")
    filename = st.text_input("업로드 파일명", value=OUTPUT_PDF_NAME)
    do_download = st.checkbox("업로드 전, 결과 PDF 다운로드 제공")
    folder_id_final = st.text_input("Drive 폴더 ID (미입력 시 My Drive)", value=folder_id_input)

    if st.button("Google Drive로 업로드", type="primary", use_container_width=True):
        if not st.session_state.pdf_bytes:
            st.error("PDF가 로드되어 있지 않습니다.")
        elif not st.session_state.values_png:
            st.error("적용된 서명이 없습니다.")
        else:
            try:
                stamped = stamp_multiple_into_pdf(
                    original_pdf_bytes=st.session_state.pdf_bytes,
                    values_png=st.session_state.values_png,
                    fields=FIELDS
                )
            except Exception as e:
                st.error(f"PDF 합성 실패: {e}")
                st.stop()

            if do_download:
                st.download_button(
                    "PDF 다운로드",
                    data=stamped,
                    file_name=filename or OUTPUT_PDF_NAME,
                    mime="application/pdf",
                )

            try:
                res = upload_to_drive(stamped, filename or OUTPUT_PDF_NAME, folder_id_final.strip())
                st.success(f"업로드 완료 · fileId: {res.get('id')}")
                if res.get("webViewLink"):
                    st.markdown(f"[웹에서 열기]({res['webViewLink']})")
            except Exception as e:
                st.error(f"Drive 업로드 실패: {e}")

st.caption("· 필드별 전용 캔버스에서만 입력되며, 적용 즉시 좌측 미리보기 이미지에 합성됩니다. 실제 PDF 스탬프는 pt 좌표로 정확히 반영됩니다.")
