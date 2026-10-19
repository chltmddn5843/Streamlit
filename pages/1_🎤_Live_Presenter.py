"""
Live Visual Presenter (1차 웹 프로토타입)
- 발화(음성) → 실시간 자막 → 키워드 정리 → 도형 슬라이드(박스/흐름/벤다이어그램/원/목차) 자동 생성
- 스크립트를 미리 주면 섹션별 슬라이드를 만들어 두고, 발화와 매칭되면 즉시 띄움
- 미리 올린 이미지는 파일명(키워드)을 말하면 바로 표시

음성 인식: 브라우저 Web Speech API (Chrome / Edge 권장, 무료, 실시간)
"""
import base64
import json
import re
from collections import Counter
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Live Visual Presenter", page_icon="🎤", layout="wide")

# ---------------------------------------------------------------------------
# 간단한 한국어 키워드 추출 (형태소 분석기 없이 동작하는 규칙 기반)
# ---------------------------------------------------------------------------
PARTICLES = sorted(
    ["에서는", "으로는", "이라는", "에서", "으로", "에게", "까지", "부터", "이란", "라는", "하고",
     "이랑", "처럼", "보다", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "랑"],
    key=len, reverse=True,
)
STOP = set("""그리고 그래서 그러면 하지만 이제 저희 우리 여러분 이번 오늘 다음 먼저 그것 이것 저것 정말 아주 바로
또한 그냥 지금 여기 거기 무엇 어떻게 왜 때문 경우 정도 부분 내용 관련 대해 통해 위해 슬라이드 말씀""".split())
VERB_END = re.compile(r"(습니다|입니다|니다|해요|어요|아요|하고|하며|해서|하는|했던|되는|하여|하면|겠습니다|됩니다|있는|없는|같은|위한|통해|대한|된다|한다)$")
TRIG_WORDS = re.compile(r"비교|차이|공통|대비|교집합|단계|절차|프로세스|흐름|순서|과정|목차|구성|항목|요소|핵심|가지|정리|요약|키워드")

RULES = [
    ("venn", r"비교|차이|공통|대비|vs|VS|교집합"),
    ("flow", r"단계|절차|프로세스|흐름|순서|파이프라인|과정"),
    ("index", r"목차|아젠다|agenda|다룰|소개할|항목"),
    ("circles", r"요소|구성요소|핵심|기둥|역량|가지"),
]


def nouns(text: str) -> list[str]:
    out = []
    for tok in re.findall(r"[가-힣A-Za-z0-9]+", text):
        t = re.sub(r"(입니다|이에요|예요|이다|이고|이며|였습니다)$", "", tok)
        if len(t) < 2:
            t = tok
        for p in PARTICLES:
            if t.endswith(p) and len(t) - len(p) >= 2:
                t = t[: -len(p)]
                break
        if len(t) < 2 or t in STOP or VERB_END.search(t):
            continue
        out.append(t)
    return out


def detect_type(text: str) -> str:
    for t, p in RULES:
        if re.search(p, text):
            return t
    return "box"


def rule_plan(script: str) -> list[dict]:
    """스크립트를 섹션(빈 줄 또는 # 제목) 단위로 나눠 슬라이드 계획을 만든다."""
    blocks = re.split(r"\n\s*\n|\n(?=#)", script.strip())
    slides = []
    for b in blocks:
        lines = [l.strip() for l in b.strip().splitlines() if l.strip()]
        if not lines:
            continue
        title = lines[0].lstrip("#").strip()[:40]
        body = lines[1:]
        bullets = [re.sub(r"^([-*•]|\d+[.)])\s*", "", l) for l in body if re.match(r"^([-*•]|\d+[.)])", l)]
        text = " ".join(lines)
        typ = detect_type(text)
        if typ == "box" and bullets:
            typ = "index"
        kws = [w for w, _ in Counter(n for n in nouns(text) if not TRIG_WORDS.search(n)).most_common(8)]
        items = [x[:20] for x in bullets[:6]] or [w for w in kws if w not in title][:4]
        slides.append({"title": title, "type": typ, "items": items, "keywords": kws, "source": "script"})
    return slides


CLAUDE_PROMPT = """너는 발표 슬라이드 설계자야. 아래 발표 스크립트를 섹션별 슬라이드 계획으로 바꿔줘.
각 슬라이드는 다음 JSON 필드를 가져:
- title: 짧은 제목 (20자 이내)
- type: "flow"(단계/프로세스), "venn"(비교/공통점), "circles"(핵심 요소), "index"(목차/나열), "box"(핵심 메시지) 중 하나
- items: 도형에 들어갈 짧은 라벨 2~6개 (각 12자 이내). venn이면 비교 대상 2~3개
- keywords: 발표자가 이 슬라이드를 말할 때 실제로 입에 올릴 단어 5~8개 (명사 위주)
JSON 배열만 출력해. 다른 설명은 쓰지 마.

스크립트:
"""


def get_secret(name: str):
    try:
        return st.secrets.get(name)
    except Exception:
        return None


def claude_plan(script: str, api_key: str) -> list[dict]:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic 패키지가 설치되어 있지 않습니다 (requirements.txt에 추가 필요)")

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": CLAUDE_PROMPT + script}],
    )
    text = msg.content[0].text
    data = json.loads(text[text.find("["): text.rfind("]") + 1])
    for s in data:
        s.setdefault("items", [])
        s.setdefault("keywords", [])
        s["source"] = "script"
        if s.get("type") not in {"flow", "venn", "circles", "index", "box"}:
            s["type"] = "box"
    return data


# ---------------------------------------------------------------------------
# 사이드바: 설정 / 스크립트 / 이미지
# ---------------------------------------------------------------------------
SAMPLE = """# 프로젝트 개요
저희 제안은 제조 현장의 품질 데이터를 실시간으로 수집하고 분석하는 플랫폼입니다.

# 처리 단계
- 데이터 수집
- 전처리
- 이상 탐지
- 대시보드

# 기존 방식과 비교
기존 수기 점검과 AI 자동 검사를 비교하면 정확도와 속도에서 차이가 납니다.

# 핵심 요소
센서, 클라우드, AI 모델 세 가지 요소가 핵심입니다."""

with st.sidebar:
    st.header("⚙️ 설정")
    lang = st.selectbox("인식 언어", ["ko-KR", "en-US"], index=0)
    threshold = st.slider("스크립트 매칭 민감도 (일치 키워드 수)", 1, 5, 2,
                          help="발화에 슬라이드 키워드가 이 개수 이상 들어가면 해당 슬라이드로 전환")

    st.header("📝 발표 스크립트 (선택)")
    script = st.text_area("섹션은 빈 줄이나 #제목으로 구분", value=SAMPLE, height=260)
    api_key = get_secret("ANTHROPIC_API_KEY")
    use_ai = st.toggle("Claude로 슬라이드 계획 만들기", value=bool(api_key), disabled=not api_key,
                       help="Streamlit secrets에 ANTHROPIC_API_KEY가 있으면 사용 가능")
    if st.button("스크립트 분석 → 슬라이드 준비", type="primary", use_container_width=True):
        with st.spinner("슬라이드 계획을 만드는 중..."):
            try:
                st.session_state.plan = claude_plan(script, api_key) if use_ai else rule_plan(script)
            except Exception as e:
                st.warning(f"AI 분석 실패, 규칙 기반으로 대체합니다: {e}")
                st.session_state.plan = rule_plan(script)
    if st.button("스크립트 비우기", use_container_width=True):
        st.session_state.plan = []

    st.header("🖼️ 미리 저장할 이미지 (선택)")
    st.caption("파일명이 키워드가 됩니다. 예) 아키텍처.png → '아키텍처'라고 말하면 표시")
    uploads = st.file_uploader("이미지 업로드", type=["png", "jpg", "jpeg", "gif", "svg"],
                               accept_multiple_files=True)

plan = st.session_state.get("plan", [])
images = {}
for f in uploads or []:
    mime = "image/svg+xml" if f.name.lower().endswith(".svg") else f.type
    images[Path(f.name).stem] = f"data:{mime};base64,{base64.b64encode(f.getvalue()).decode()}"
for s in plan:
    for name in images:
        if name in s["title"] or name in s.get("keywords", []):
            s["image"] = name

# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
st.title("🎤 Live Visual Presenter")
st.caption("말하면 슬라이드가 그려집니다 · Chrome/Edge에서 🎤 시작을 누르고 마이크를 허용하세요 · "
           "음성 없이도 아래 입력창에 문장을 쳐서 테스트할 수 있어요")

with st.expander(f"준비된 스크립트 슬라이드 {len(plan)}개 · 말해볼 수 있는 예시 문장", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        for i, s in enumerate(plan, 1):
            st.markdown(f"**{i}. {s['title']}** `{s['type']}` — 키워드: {', '.join(s.get('keywords', [])[:6])}")
        if not plan:
            st.write("사이드바에서 '스크립트 분석'을 누르면 여기에 표시됩니다.")
    with c2:
        st.markdown("""
- "데이터 수집, 전처리, 모델링 **단계**로 진행됩니다" → 흐름도
- "기존 방식**과** AI 방식을 **비교**하면" → 벤다이어그램
- "핵심 **요소**는 세 가지입니다" → "첫 번째 센서" "두 번째 클라우드" 로 하나씩 추가
- "오늘 **목차**를 소개하겠습니다" → 목차 슬라이드
- "지금까지 내용을 **정리**하면" → 핵심 키워드 카드
- "**다음 슬라이드**" / "**이전 슬라이드**" → 이동
""")

config = {"slides": plan, "images": images, "lang": lang, "threshold": threshold}
html = (Path(__file__).resolve().parent.parent / "presenter.html").read_text(encoding="utf-8")
html = html.replace("__CONFIG__", json.dumps(config, ensure_ascii=False).replace("</", "<\\/"))
components.html(html, height=780, scrolling=False)
