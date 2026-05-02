#!/usr/bin/env bash
# AdaptiveAgent 서비스 시행 스크립트
# 사용법:
#   ./scripts/start.sh                     # 대화형 CLI 실행
#   ./scripts/start.sh --check-only        # 환경 점검만 수행
#   ./scripts/start.sh --provider openai   # OpenAI provider로 실행
#   ./scripts/start.sh --provider gemini   # Gemini provider로 실행
#   ./scripts/start.sh --provider ollama   # Ollama provider로 실행
#   ./scripts/start.sh "작업 내용"          # 단일 작업 실행 후 종료

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
ENV_FILE="$REPO_ROOT/.env"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*" >&2; exit 1; }
info() { echo -e "  $*"; }

# ── 인자 파싱 ────────────────────────────────────────────
CHECK_ONLY=false
PROVIDER=""
TASK=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check-only) CHECK_ONLY=true; shift ;;
        --provider)   PROVIDER="$2"; shift 2 ;;
        -*)           fail "알 수 없는 옵션: $1" ;;
        *)            TASK="$1"; shift ;;
    esac
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AdaptiveAgent 서비스 시행 스크립트"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Python 버전 확인 ───────────────────────────────────
echo "[1/5] Python 버전 확인"
PYTHON=$(command -v python3 || command -v python || fail "Python을 찾을 수 없습니다.")
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ]]; then
    fail "Python 3.10 이상이 필요합니다. 현재: $PY_VERSION"
fi
ok "Python $PY_VERSION"

# ── 2. 가상환경 및 의존성 ─────────────────────────────────
echo ""
echo "[2/5] 의존성 확인"
if [[ ! -d "$VENV_DIR" ]]; then
    warn ".venv 없음 — 가상환경 생성 중..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
[[ ! -f "$VENV_PYTHON" ]] && VENV_PYTHON="$VENV_DIR/Scripts/python"  # Windows fallback
[[ ! -f "$VENV_PYTHON" ]] && fail "가상환경 Python을 찾을 수 없습니다: $VENV_DIR"

"$VENV_PYTHON" -m pip install -q -r "$REPO_ROOT/requirements.txt" \
    && ok "requirements.txt 설치 완료" \
    || fail "의존성 설치 실패"

"$VENV_PYTHON" -m pip install -q -e "$REPO_ROOT" \
    && ok "adaptive_agent 패키지 설치 완료" \
    || fail "패키지 설치 실패"

# ── 3. .env 로드 및 API 키 확인 ───────────────────────────
echo ""
echo "[3/5] 환경 변수 확인"
if [[ -f "$ENV_FILE" ]]; then
    set -o allexport
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +o allexport
    ok ".env 로드 완료"
else
    warn ".env 파일 없음 — API 키가 환경변수로 설정되어 있는지 확인하세요."
fi

# Provider 결정
if [[ -z "$PROVIDER" ]]; then
    if [[ -n "${OPENAI_API_KEY:-}" && "$OPENAI_API_KEY" != *placeholder* && "$OPENAI_API_KEY" != *your_* ]]; then
        PROVIDER="openai"
    elif [[ -n "${GEMINI_API_KEY:-}" && "$GEMINI_API_KEY" != *placeholder* && "$GEMINI_API_KEY" != *your_* ]]; then
        PROVIDER="gemini"
    elif command -v ollama &>/dev/null; then
        PROVIDER="ollama"
    else
        warn "사용 가능한 LLM provider를 찾지 못했습니다."
        warn "OPENAI_API_KEY, GEMINI_API_KEY 또는 Ollama 중 하나를 설정하세요."
        PROVIDER="ollama"
    fi
fi
ok "LLM provider: $PROVIDER"

# ── 4. 단위 테스트 ────────────────────────────────────────
echo ""
echo "[4/5] 단위 테스트 실행"
if "$VENV_PYTHON" -m pytest "$REPO_ROOT/tests/" -q --tb=short 2>&1 | tail -3; then
    ok "단위 테스트 통과"
else
    fail "단위 테스트 실패 — 로그를 확인하세요."
fi

# ── 5. LLM 연결 스모크 테스트 ─────────────────────────────
echo ""
echo "[5/5] LLM 연결 확인 (provider: $PROVIDER)"
SMOKE_PROMPT="Reply with exactly one word: ready"

SMOKE_RESULT=$("$VENV_PYTHON" -m adaptive_agent \
    --json --llm "$PROVIDER" "$SMOKE_PROMPT" 2>/dev/null \
    | "$PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d.get('action','?'))" 2>/dev/null \
    || echo "error")

if [[ "$SMOKE_RESULT" == "llm" || "$SMOKE_RESULT" == "tool" ]]; then
    ok "LLM 연결 확인 완료 (action=$SMOKE_RESULT)"
elif [[ "$SMOKE_RESULT" == "llm_error" || "$SMOKE_RESULT" == "error" ]]; then
    warn "LLM 연결 실패 — API 키 또는 네트워크를 확인하세요."
    if [[ "$CHECK_ONLY" == "true" ]]; then
        exit 1
    fi
else
    ok "LLM 응답 확인 (action=$SMOKE_RESULT)"
fi

# ── 완료 ────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "AdaptiveAgent 준비 완료"
echo ""

if [[ "$CHECK_ONLY" == "true" ]]; then
    info "환경 점검 완료. 서비스 실행은 --check-only 없이 다시 실행하세요."
    exit 0
fi

info "Provider : $PROVIDER"
info "실행 예시 :"
info "  $VENV_PYTHON -m adaptive_agent --llm $PROVIDER \"작업 내용\""
info "  $VENV_PYTHON -m adaptive_agent --llm $PROVIDER --json \"작업 내용\""
info "  $VENV_PYTHON -m adaptive_agent --list-tools"
echo ""

if [[ -n "$TASK" ]]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  작업 실행: $TASK"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exec "$VENV_PYTHON" -m adaptive_agent --llm "$PROVIDER" "$TASK"
else
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  대화형 모드 시작 (Ctrl+C로 종료)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    while true; do
        echo -n "작업 입력 > "
        read -r INPUT || break
        [[ -z "$INPUT" ]] && continue
        [[ "$INPUT" == "exit" || "$INPUT" == "quit" || "$INPUT" == "종료" ]] && break
        "$VENV_PYTHON" -m adaptive_agent --llm "$PROVIDER" "$INPUT"
        echo ""
    done
    echo ""
    ok "AdaptiveAgent 종료"
fi
