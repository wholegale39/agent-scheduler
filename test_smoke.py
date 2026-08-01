"""Smoke test for agent-scheduler — verifies core scheduler logic."""
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from models import TaskDef, TaskType, DeliveryTarget
from scheduler import AgentScheduler, parse_schedule


def test_parse_schedule():
    s = parse_schedule("0 18 * * 1-5")
    assert s is not None
    assert "cron" in s or hasattr(s, "cron")


def test_task_def_model():
    t = TaskDef(
        id="test-1",
        name="收盘汇总",
        prompt="生成收盘汇总",
        schedule="0 18 * * 1-5",
        type=TaskType.cron,
        delivery=[DeliveryTarget(platform="feishu", chat_id="oc_test")],
    )
    assert t.id == "test-1"
    assert t.schedule == "0 18 * * 1-5"


def test_scheduler_init():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "config.yaml")
        s = AgentScheduler(config_path=cfg)
        assert s is not None


if __name__ == "__main__":
    test_parse_schedule()
    test_task_def_model()
    test_scheduler_init()
    print("✅ all smoke tests passed")
