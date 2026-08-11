"""Credit Engine tests: wallet math, zero-credit lanes, dedup, rollover,
BYOK, license activation, preview accuracy, export, and client metering."""
import json

import pytest

try:
    from backend.core import credits as credits_mod
    from backend.core.credits import CreditEngine
except ImportError:  # running with backend/ as the working directory
    from core import credits as credits_mod  # type: ignore[no-redef]
    from core.credits import CreditEngine  # type: ignore[no-redef]


CONFIG = {
    "enabled": True,
    "rate_credits_per_usd": 100,
    "plans": {
        "free": {"credits": 300, "price_usd": 0, "trial_days": 14},
        "pro": {"credits": 2000, "price_usd": 20},
        "pro_plus": {"credits": 6000, "price_usd": 60},
        "ultra": {"credits": 20000, "price_usd": 200},
    },
    "packs": {"starter": {"credits": 1500, "price_usd": 20, "validity_days": 30}},
    "rollover_fraction": 0.25,
    "rollover_validity_days": 90,
    "license_keys": [{"key": "TEST-LIC-1234", "plan": "ultra"}],
}


@pytest.fixture
def engine(tmp_path):
    return CreditEngine(tmp_path / "credits.db", CONFIG)


# --- wallet math ----------------------------------------------------------- #

def test_initial_wallet(engine):
    bal = engine.balance()
    assert bal["enabled"] is True
    assert bal["balance_credits"] == 300.0
    assert bal["plan"] == "free"
    assert bal["byok"] is False


def test_meter_debits_credits(engine):
    engine.meter(0.35, "deepseek/deepseek-v4-flash", "chat", 1000, 500)
    assert engine.balance()["balance_credits"] == pytest.approx(265.0)
    rows = engine.ledger(10)
    assert rows[0]["reason"] == "usage"
    assert rows[0]["delta"] == pytest.approx(-35.0)
    assert rows[0]["model"] == "deepseek/deepseek-v4-flash"


def test_minimum_charge_for_micro_calls(engine):
    engine.meter(0.000001, "deepseek/deepseek-v4-flash", "chat", 1, 1)
    assert engine.balance()["balance_credits"] == pytest.approx(299.99)


def test_cache_hit_is_zero_and_counts_savings(engine):
    engine.meter(0.0, "deepseek/deepseek-v4-flash", "chat_cached",
                 1000, 500, avoided_usd=0.014)
    assert engine.balance()["balance_credits"] == 300.0  # nothing debited
    rows = engine.ledger(10)
    assert rows[0]["reason"] == "cache_hit"
    analytics = engine.analytics()
    assert analytics["cache_hits"] == 1
    assert analytics["cache_savings_usd"] == pytest.approx(0.014)


def test_silent_zero_cost_rows(engine):
    before = len(engine.ledger(10))  # the initial grant row exists
    engine.meter(0.0, "local/qwen3:8b", "swarm_direct")  # no cache marker
    assert len(engine.ledger(10)) == before


def test_dedup_prevents_double_charge(engine):
    engine.meter(0.20, "deepseek/deepseek-v4-flash", "chat", 500, 250)
    engine.meter(0.20, "deepseek/deepseek-v4-flash", "chat", 500, 250)
    assert engine.balance()["balance_credits"] == pytest.approx(280.0)


def test_byok_bypasses_wallet(engine):
    engine.set_byok(True)
    engine.meter(10.0, "deepseek/deepseek-v4-pro", "chat", 1000, 500)
    assert engine.balance()["balance_credits"] == 300.0
    assert engine.balance()["byok"] is True
    rows = engine.ledger(10)
    assert not any(r["model"] == "deepseek/deepseek-v4-pro" for r in rows)


# --- plans, packs, licenses ------------------------------------------------ #

def test_add_pack(engine):
    result = engine.add_pack("starter")
    assert result["ok"] is True
    assert engine.balance()["balance_credits"] == pytest.approx(1800.0)
    assert engine.add_pack("nope")["ok"] is False


def test_set_plan(engine):
    result = engine.set_plan("pro")
    assert result["ok"] is True
    bal = engine.balance()
    assert bal["plan"] == "pro"
    assert bal["monthly_limit"] == 2000.0
    assert engine.set_plan("bogus")["ok"] is False


def test_license_activation(engine):
    assert engine.activate_license("TEST-LIC-1234")["ok"] is True
    assert engine.balance()["plan"] == "ultra"
    assert engine.activate_license("BAD-KEY")["ok"] is False


# --- rollover -------------------------------------------------------------- #

def test_monthly_rollover_caps_at_fraction(engine):
    engine.set_plan("pro")  # grants 2000 on top of the free 300 -> 2300
    engine.meter(5.0, "deepseek/deepseek-v4-flash", "chat", 1000, 500)  # -500
    # Simulate a month boundary: previous period, 500 credits used.
    with engine._connect() as conn:
        conn.execute(
            "UPDATE credit_wallet SET plan_period = '2000-01', monthly_used = 500 "
            "WHERE id = 1"
        )
    bal = engine.balance()  # triggers the rollover
    # unused = 2000 - 500 = 1500 -> roll 25% = 375 into the balance
    assert bal["rollover_credits"] == pytest.approx(375.0)
    assert bal["balance_credits"] == pytest.approx(2300.0 - 500.0 + 375.0)
    rows = engine.ledger(10)
    assert any(r["reason"] == "rollover" and r["delta"] == pytest.approx(375.0)
               for r in rows)


# --- preview + export ------------------------------------------------------ #

def test_cost_preview_matches_pricing(engine):
    # deepseek-v4-flash: $0.14/M in, $0.28/M out. 400 chars -> 100 in-tokens.
    cost = engine.estimate_cost_usd("deepseek/deepseek-v4-flash", "x" * 400, 100)
    expected = (100 * 0.14 + 100 * 0.28) / 1_000_000.0
    assert cost == pytest.approx(round(expected, 6))
    assert engine.estimate_cost_usd("no/such-model", "x", 10) == 0.0


def test_export_shapes(engine):
    engine.meter(0.10, "deepseek/deepseek-v4-flash", "chat", 100, 50)
    csv_out = engine.export("csv")
    assert csv_out.startswith("ts,delta,reason")
    assert "usage" in csv_out
    json_out = json.loads(engine.export("json"))
    assert json_out and json_out[0]["model"] == "deepseek/deepseek-v4-flash"


def test_analytics_shape(engine):
    engine.meter(1.0, "deepseek/deepseek-v4-flash", "chat", 1000, 500)
    engine.meter(2.0, "deepseek/deepseek-v4-flash", "swarm_direct", 2000, 1000)
    analytics = engine.analytics()
    assert analytics["total_spend_credits"] == pytest.approx(300.0)
    assert analytics["per_feature"]["chat"] == pytest.approx(100.0)
    assert analytics["per_feature"]["swarm_direct"] == pytest.approx(200.0)
    assert "forecast_30d_credits" in analytics
    assert isinstance(analytics["alerts"], list)


def test_disabled_engine(tmp_path):
    eng = CreditEngine(tmp_path / "credits.db", {"enabled": False})
    assert eng.balance() == {"enabled": False}
    eng.meter(5.0, "deepseek/deepseek-v4-flash", "chat", 1, 1)  # no-op, no raise


# --- metering through the OpenRouter client -------------------------------- #

class _FakeUsage:
    prompt_tokens = 12
    completion_tokens = 7


class _FakeMsg:
    content = "hello"
    reasoning_content = None


class _FakeChoice:
    message = _FakeMsg()
    finish_reason = "stop"


class _FakeResp:
    choices = [_FakeChoice()]
    usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return _FakeResp()


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self):
        self.chat = _FakeChat()


def test_client_metering_and_cache_hit_savings(tmp_path, monkeypatch):
    try:
        from backend.tools import openrouter_client as orc
    except ImportError:
        from tools import openrouter_client as orc  # type: ignore[no-redef]

    engine = CreditEngine(tmp_path / "credits.db", CONFIG)
    credits_mod.configure(engine)
    try:
        monkeypatch.setenv("INFINITY_LLM_CACHE_DIR", str(tmp_path / "llm_cache"))
        route = orc.Route(provider="deepseek", key="sk-ds-TEST",
                          base_url="http://127.0.0.1:9/v1", source="test",
                          free_only=False)
        monkeypatch.setattr(orc, "collect_routes", lambda *a, **k: [route])
        fake = _FakeClient()
        monkeypatch.setattr(orc.OpenRouterClient, "_client_for",
                            lambda self, route: fake)
        client = orc.OpenRouterClient()
        msgs = [{"role": "user", "content": "hello"}]
        client.chat("deepseek/deepseek-v4-flash", list(msgs), max_tokens=50)
        client.chat("deepseek/deepseek-v4-flash", list(msgs), max_tokens=50)
        # First call: minimum 0.01 credit (micro-call). Second: cache hit, 0.
        assert engine.balance()["balance_credits"] == pytest.approx(299.99)
        analytics = engine.analytics()
        assert analytics["cache_hits"] == 1
    finally:
        credits_mod.configure(None)
