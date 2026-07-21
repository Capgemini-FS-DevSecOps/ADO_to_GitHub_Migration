"""Unit tests for metrics collector (spec 011)."""
from ado2gh.agents.metrics import MetricsCollector


class TestMetricsCollector:
    def test_counter_increment(self):
        m = MetricsCollector()
        m.inc_counter("test_counter")
        m.inc_counter("test_counter", 2)
        text = m.to_prometheus_text()
        assert "test_counter 3" in text

    def test_gauge(self):
        m = MetricsCollector()
        m.set_gauge("active_sessions", 5)
        text = m.to_prometheus_text()
        assert "active_sessions 5" in text

    def test_histogram(self):
        m = MetricsCollector()
        m.observe_histogram("llm_call_duration_seconds", 0.5)
        m.observe_histogram("llm_call_duration_seconds", 1.5)
        text = m.to_prometheus_text()
        assert "llm_call_duration_seconds_count 2" in text
        assert "llm_call_duration_seconds_sum 2.0" in text

    def test_session_status_counts(self):
        m = MetricsCollector()
        m.set_session_status("idle", 3)
        m.set_session_status("planning", 2)
        text = m.to_prometheus_text()
        assert 'session_status_counts{status="idle"} 3' in text
        assert 'session_status_counts{status="planning"} 2' in text

    def test_record_llm_call(self):
        m = MetricsCollector()
        m.record_llm_call(1.2)
        text = m.to_prometheus_text()
        assert "llm_calls_total 1" in text
        assert "llm_call_duration_seconds_count 1" in text

    def test_record_pev_cycle(self):
        m = MetricsCollector()
        m.record_pev_cycle()
        m.record_pev_cycle()
        text = m.to_prometheus_text()
        assert "pev_cycles_total 2" in text

    def test_record_guardrail_block(self):
        m = MetricsCollector()
        m.record_guardrail_block()
        text = m.to_prometheus_text()
        assert "guardrail_blocks_total 1" in text
