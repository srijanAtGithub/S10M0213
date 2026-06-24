import asyncio
import re
import yaml
from pathlib import Path

from datetime import datetime, timedelta

SICILY_HOME = Path.home() / ".sicily"
YAML_FILE = SICILY_HOME / "Recurring_Tasks" / "recurring_tasks.yaml"

_dispatch = None

def set_dispatch(fn):
    global _dispatch
    _dispatch = fn

VALID_DAYS = {
    "mon", "tue", "wed", "thu", "fri", "sat", "sun"
}

WEEKDAY_MAP = {
    0: "mon",
    1: "tue",
    2: "wed",
    3: "thu",
    4: "fri",
    5: "sat",
    6: "sun",
}


# PUBLIC ENTRYPOINT
async def start_recurring_tasks():

    tasks = load_and_validate_yaml()

    enabled_tasks = [t for t in tasks if t["enabled"]]

    print(f"🔁 Starting {len(enabled_tasks)} recurring task(s)...")

    for task_config in enabled_tasks:
        asyncio.create_task(task_runner(task_config))


# YAML LOADING + VALIDATION
def load_and_validate_yaml():

    with open(YAML_FILE, "r") as f:
        data = yaml.safe_load(f)

    if not data or "tasks" not in data:
        raise ValueError("Missing 'tasks' in YAML.")

    tasks = data["tasks"]

    if not isinstance(tasks, list):
        raise ValueError("'tasks' must be a list.")

    seen_ids = set()

    for task in tasks:

        required_fields = ["id", "enabled", "task", "schedule"]

        for field in required_fields:
            if field not in task:
                raise ValueError(f"Task missing required field: {field}")

        task_id = task["id"]

        if task_id in seen_ids:
            raise ValueError(f"Duplicate task id: {task_id}")

        seen_ids.add(task_id)

        validate_schedule(task_id, task["schedule"])

    return tasks


def validate_schedule(task_id, schedule):

    if "mode" not in schedule:
        raise ValueError(f"[{task_id}] Missing schedule.mode")

    mode = schedule["mode"]

    if mode not in ["daily", "interval"]:
        raise ValueError(f"[{task_id}] Invalid mode: {mode}")

    # ── DAILY ────────────────────────────────────────────────
    if mode == "daily":

        if "at" not in schedule:
            raise ValueError(f"[{task_id}] daily mode requires 'at'")

        validate_time(schedule["at"], task_id)

    # ── INTERVAL ─────────────────────────────────────────────
    elif mode == "interval":

        if "every" not in schedule:
            raise ValueError(f"[{task_id}] interval mode requires 'every'")

        validate_interval(schedule["every"], task_id)

        if "start" in schedule:
            validate_time(schedule["start"], task_id)

    # ── DAYS ────────────────────────────────────────────────
    if "days" in schedule:

        if not isinstance(schedule["days"], list):
            raise ValueError(f"[{task_id}] days must be a list")

        for d in schedule["days"]:
            if d not in VALID_DAYS:
                raise ValueError(f"[{task_id}] Invalid day: {d}")


def validate_time(value, task_id):

    if not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError(f"[{task_id}] Invalid time format: {value}")

    hour, minute = map(int, value.split(":"))

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"[{task_id}] Invalid time: {value}")


def validate_interval(value, task_id):

    if not re.fullmatch(r"\d+[mh]", value):
        raise ValueError(f"[{task_id}] Invalid interval: {value}")


# TASK RUNNER
async def task_runner(task_config):

    task_id = task_config["id"]
    task_text = task_config["task"]
    schedule = task_config["schedule"]

    mode = schedule["mode"]

    print(f"✅ Task loaded: {task_id}")

    while True:

        now = datetime.now()

        # DAILY MODE
        if mode == "daily":

            run_time = build_today_datetime(schedule["at"])

            if run_time <= now:
                run_time += timedelta(days=1)

            wait_seconds = (run_time - now).total_seconds()

            await asyncio.sleep(wait_seconds)

            if should_run_today(schedule):
                await execute_task(task_id, task_text)

        # INTERVAL MODE
        elif mode == "interval":

            if "start" in schedule:

                first_run = build_today_datetime(schedule["start"])

                if first_run > now:
                    wait_seconds = (first_run - now).total_seconds()
                    await asyncio.sleep(wait_seconds)

            if should_run_today(schedule):
                await execute_task(task_id, task_text)

            interval_seconds = parse_interval(schedule["every"])

            await asyncio.sleep(interval_seconds)


# HELPERS
async def execute_task(task_id, task_text):

    print(
        f"\n🔁 TASK EXECUTED"
        f"\n🆔 ID   : {task_id}"
        f"\n📝 Task : {task_text}"
        f"\n🕒 Time : {datetime.now()}\n",
        flush=True
    )

    if _dispatch is None:
        print(f"⚠️  No dispatch set — skipping task {task_id}")
        return

    await _dispatch(task_id, task_text)


def build_today_datetime(time_str):

    hour, minute = map(int, time_str.split(":"))

    now = datetime.now()

    return now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0
    )


def parse_interval(value):

    amount = int(value[:-1])
    unit = value[-1]

    if unit == "m":
        return amount * 60

    if unit == "h":
        return amount * 60 * 60

    raise ValueError(f"Invalid interval unit: {value}")


def should_run_today(schedule):

    if "days" not in schedule:
        return True

    today = WEEKDAY_MAP[datetime.now().weekday()]

    return today in schedule["days"]
