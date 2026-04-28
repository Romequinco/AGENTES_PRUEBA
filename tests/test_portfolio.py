"""Tests de integración del portfolio tracker global (Sprint 2).

Usa mocks para get_quote y get_historical — no llama APIs externas.
Requiere una DB SQLite en memoria vía monkeypatch de DATABASE_URL.
"""

import os
import sys
import datetime
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Fixtures base
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    """Flask app configurada para tests con SQLite en memoria."""
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-portfolio-tests-32chars")
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    os.environ.setdefault("SENDGRID_API_KEY", "SG.fake")
    os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_fake")
    os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_fake")

    # Parchear DATABASE_URL antes de que db.models lo lea
    import db.models as _models_mod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _models_mod.engine = test_engine
    _models_mod.SessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    _models_mod.Base.metadata.create_all(bind=test_engine)

    from api.flask_app import create_app
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_token(client):
    """Registra un usuario de prueba y devuelve su JWT."""
    r = client.post("/auth/register", json={"email": "portfolio@test.com", "password": "pass1234"})
    if r.status_code == 409:
        r = client.post("/auth/login", json={"email": "portfolio@test.com", "password": "pass1234"})
    data = r.get_json()
    return data["access_token"]


@pytest.fixture()
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(autouse=True)
def mock_quote(monkeypatch):
    """Mock de get_quote: devuelve precio 200.0 para cualquier símbolo."""
    def _fake_quote(symbol, asset_type="stock"):
        return {"price": 200.0, "change_pct": 1.5, "prev_close": 198.0, "source": "mock", "cached": False}
    monkeypatch.setattr("services.portfolio_tracker.get_quote", _fake_quote)
    # También lo parchea en market_data por si se importa desde allí
    try:
        import services.market_data as md
        monkeypatch.setattr(md, "get_quote", _fake_quote)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def mock_historical(monkeypatch):
    """Mock de get_historical: retorna benchmark con retorno +5%."""
    def _fake_hist(symbol, period="1m"):
        return [{"close": 100.0}, {"close": 105.0}]
    monkeypatch.setattr("services.portfolio_tracker.get_historical", _fake_hist, raising=False)
    try:
        import services.market_data as md
        monkeypatch.setattr(md, "get_historical", _fake_hist)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. CRUD de posiciones vía API
# ---------------------------------------------------------------------------

def test_create_position(client, auth_headers):
    r = client.post("/api/v1/portfolio/positions", json={
        "symbol": "AAPL",
        "asset_type": "stock",
        "quantity": 10,
        "entry_price": 150.0,
        "entry_date": "2024-01-15",
        "exchange": "NASDAQ",
    }, headers=auth_headers)
    assert r.status_code == 201
    data = r.get_json()
    assert data["symbol"] == "AAPL"
    assert data["asset_type"] == "stock"
    assert data["quantity"] == 10.0
    assert data["entry_price"] == 150.0
    assert data["exchange"] == "NASDAQ"


def test_list_positions(client, auth_headers):
    # Crear una posición primero
    client.post("/api/v1/portfolio/positions", json={
        "symbol": "MSFT", "asset_type": "stock",
        "quantity": 5, "entry_price": 300.0, "entry_date": "2024-02-01",
    }, headers=auth_headers)

    r = client.get("/api/v1/portfolio/positions", headers=auth_headers)
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    symbols = [p["symbol"] for p in data]
    assert "MSFT" in symbols


def test_edit_position(client, auth_headers):
    # Crear posición
    r = client.post("/api/v1/portfolio/positions", json={
        "symbol": "GLD", "asset_type": "commodity",
        "quantity": 3, "entry_price": 180.0, "entry_date": "2024-03-01",
    }, headers=auth_headers)
    pos_id = r.get_json()["id"]

    # Editar cantidad
    r2 = client.put(f"/api/v1/portfolio/positions/{pos_id}", json={"quantity": 7.0}, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.get_json()["quantity"] == 7.0


def test_delete_position(client, auth_headers):
    # Crear posición
    r = client.post("/api/v1/portfolio/positions", json={
        "symbol": "BTC-USD", "asset_type": "crypto",
        "quantity": 0.5, "entry_price": 40000.0, "entry_date": "2024-04-01",
    }, headers=auth_headers)
    pos_id = r.get_json()["id"]

    # Eliminar
    r2 = client.delete(f"/api/v1/portfolio/positions/{pos_id}", headers=auth_headers)
    assert r2.status_code == 204

    # Verificar que ya no existe
    r3 = client.get("/api/v1/portfolio/positions", headers=auth_headers)
    ids = [p["id"] for p in r3.get_json()]
    assert pos_id not in ids


# ---------------------------------------------------------------------------
# 2. Cálculo de P&L con precios mockeados
# ---------------------------------------------------------------------------

def test_pnl_calculation(client, auth_headers):
    """Posición comprada a 100, precio actual mockeado a 200 con qty=10 → pnl=1000, pnl_pct=100%."""
    r = client.post("/api/v1/portfolio/positions", json={
        "symbol": "TSLA", "asset_type": "stock",
        "quantity": 10, "entry_price": 100.0, "entry_date": "2024-05-01",
    }, headers=auth_headers)
    assert r.status_code == 201

    r2 = client.get("/api/v1/portfolio/positions", headers=auth_headers)
    positions = r2.get_json()
    tsla = next((p for p in positions if p["symbol"] == "TSLA"), None)
    assert tsla is not None
    assert tsla["current_price"] == 200.0
    assert tsla["pnl"] == pytest.approx(1000.0, abs=0.01)
    assert tsla["pnl_pct"] == pytest.approx(100.0, abs=0.01)


# ---------------------------------------------------------------------------
# 3. /summary con cartera mixta (stock + crypto + etf)
# ---------------------------------------------------------------------------

def test_summary_mixed_portfolio(client, auth_headers):
    # Añadir posiciones de distintas clases
    for payload in [
        {"symbol": "NVDA", "asset_type": "stock", "quantity": 2, "entry_price": 500.0, "entry_date": "2024-01-10"},
        {"symbol": "ETH-USD", "asset_type": "crypto", "quantity": 1, "entry_price": 2000.0, "entry_date": "2024-01-10"},
        {"symbol": "QQQ", "asset_type": "etf", "quantity": 5, "entry_price": 400.0, "entry_date": "2024-01-10"},
    ]:
        client.post("/api/v1/portfolio/positions", json=payload, headers=auth_headers)

    r = client.get("/api/v1/portfolio/summary", headers=auth_headers)
    assert r.status_code == 200
    data = r.get_json()

    assert "pnl_by_asset_type" in data
    assert "allocation" in data
    assert "total_pnl" in data
    assert "benchmark_return_pct" in data

    # Las 3 clases de activo deben aparecer
    asset_types_present = set(data["pnl_by_asset_type"].keys())
    assert "stock" in asset_types_present
    assert "crypto" in asset_types_present
    assert "etf" in asset_types_present

    # La allocation debe sumar ~100%
    total_alloc = sum(data["allocation"].values())
    assert abs(total_alloc - 100.0) < 0.1

    # benchmark_return_pct debe ser un float (viene del mock +5%)
    assert isinstance(data["benchmark_return_pct"], float)
    assert data["benchmark_return_pct"] == pytest.approx(5.0, abs=0.01)


def test_summary_benchmark_param(client, auth_headers):
    """El parámetro benchmark se pasa correctamente y se refleja en la respuesta."""
    r = client.get("/api/v1/portfolio/summary?benchmark=^IBEX", headers=auth_headers)
    assert r.status_code == 200
    data = r.get_json()
    assert data["benchmark"] == "^IBEX"
    assert data["benchmark_label"] == "IBEX 35"


# ---------------------------------------------------------------------------
# 4. Validación: symbol inexistente devuelve error 400
# ---------------------------------------------------------------------------

def test_invalid_symbol_returns_400(client, auth_headers, monkeypatch):
    """Si get_quote devuelve None el endpoint debe responder 400."""
    monkeypatch.setattr("services.portfolio_tracker.get_quote", lambda s, t="stock": None)

    r = client.post("/api/v1/portfolio/positions", json={
        "symbol": "XYZZZZ_FAKE", "asset_type": "stock",
        "quantity": 1, "entry_price": 10.0, "entry_date": "2024-06-01",
    }, headers=auth_headers)
    assert r.status_code == 400
    data = r.get_json()
    assert "error" in data
    assert "no encontrado" in data["error"].lower() or "symbol" in data["error"].lower()


def test_invalid_asset_type_returns_400(client, auth_headers):
    r = client.post("/api/v1/portfolio/positions", json={
        "symbol": "AAPL", "asset_type": "forex",
        "quantity": 1, "entry_price": 100.0, "entry_date": "2024-01-01",
    }, headers=auth_headers)
    assert r.status_code == 400


def test_missing_fields_returns_400(client, auth_headers):
    r = client.post("/api/v1/portfolio/positions", json={"symbol": "AAPL"}, headers=auth_headers)
    assert r.status_code == 400


def test_unauthenticated_returns_401(client):
    r = client.get("/api/v1/portfolio/positions")
    assert r.status_code == 401
