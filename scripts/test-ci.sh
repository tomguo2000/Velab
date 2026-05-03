#!/bin/bash
# 本地 CI 模拟：推送前验证 Backend + Frontend 测试，避免 CI 失败
# 用法：bash scripts/test-ci.sh
# 依赖：本地已运行 PostgreSQL（fota_db_test 库）+ Redis

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
WEB_DIR="$ROOT_DIR/web"
VENV="$BACKEND_DIR/venv/bin/activate"
[ -f "$BACKEND_DIR/.venv/bin/activate" ] && VENV="$BACKEND_DIR/.venv/bin/activate"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }
info() { echo -e "${YELLOW}▶ $1${NC}"; }

# ── macOS / Linux 工具路径自适应 ──
OS_TYPE="$(uname -s)"
if [[ "$OS_TYPE" == "Darwin" ]]; then
  # Homebrew 优先（Apple Silicon 或 Intel）
  HOMEBREW_PREFIX="${HOMEBREW_PREFIX:-$(brew --prefix 2>/dev/null || echo /usr/local)}"
  export PATH="$HOMEBREW_PREFIX/bin:$PATH"
  REDIS_START_HINT="brew services start redis"
  PG_START_HINT="brew services start postgresql@14  (或对应版本)"
else
  REDIS_START_HINT="sudo systemctl start redis"
  PG_START_HINT="sudo systemctl start postgresql"
fi

# ── 前置检查：PostgreSQL fota_db_test ──
info "检查 PostgreSQL fota_db_test..."
PG_CONN="-U postgres -h 127.0.0.1"
if PGPASSWORD=fota_password psql $PG_CONN -c '\q' fota_db_test &>/dev/null 2>&1; then
  pass "PostgreSQL fota_db_test 可连接"
else
  info "尝试创建测试库..."
  PGPASSWORD=fota_password psql $PG_CONN -c "CREATE DATABASE fota_db_test;" 2>/dev/null || true
  PGPASSWORD=fota_password psql $PG_CONN -c '\q' fota_db_test &>/dev/null || fail "PostgreSQL fota_db_test 不可用，请确认 PostgreSQL 已启动：$PG_START_HINT"
  pass "PostgreSQL fota_db_test 已创建"
fi

# ── 前置检查：Redis ──
info "检查 Redis..."
redis-cli ping &>/dev/null || fail "Redis 未运行，请先启动：$REDIS_START_HINT"
pass "Redis 运行正常"

# ══════════════════════════════════════════════
# BACKEND
# ══════════════════════════════════════════════
info "═══ Backend Tests ═══"
# shellcheck source=/dev/null
source "$VENV"

cd "$BACKEND_DIR"

VENV_BIN="$(dirname "$VENV")"

info "安装 CI 额外依赖（flake8 等）..."
"$VENV_BIN/pip" install -q flake8 pytest-cov 2>&1 | tail -3

info "Lint (flake8 严格模式)..."
"$VENV_BIN/flake8" . --count --select=E9,F63,F7,F82 --show-source --statistics \
  --exclude=venv,.venv || fail "flake8 严格检查失败"
pass "flake8 严格检查通过"

info "Lint (flake8 宽松模式)..."
"$VENV_BIN/flake8" . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics \
  --exclude=venv,.venv
pass "flake8 宽松检查完成"

info "运行 pytest（复刻 CI 环境变量）..."
mkdir -p test_data
TEST_DATABASE_URL="postgresql://postgres:fota_password@localhost:5432/fota_db_test" \
POSTGRES_DB="fota_db_test" \
POSTGRES_USER="postgres" \
POSTGRES_PASSWORD="fota_password" \
POSTGRES_HOST="localhost" \
POSTGRES_PORT="5432" \
STORAGE_ROOT="./test_data" \
PYTHONPATH="." \
  "$VENV_BIN/pytest" tests/ log_pipeline/tests/ -v --cov=api --cov=models --cov=services --cov=log_pipeline || fail "Backend 测试失败"
pass "Backend 测试通过"

# ══════════════════════════════════════════════
# FRONTEND
# ══════════════════════════════════════════════
info "═══ Frontend Tests ═══"
cd "$WEB_DIR"

info "Lint (eslint)..."
npm run lint || fail "ESLint 失败"
pass "ESLint 通过"

info "运行 vitest..."
npm run test:ci || fail "Frontend 测试失败"
pass "Frontend 测试通过"

echo ""
echo -e "${GREEN}══════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ 本地 CI 全部通过，可以提交 PR！ ${NC}"
echo -e "${GREEN}══════════════════════════════════════${NC}"
