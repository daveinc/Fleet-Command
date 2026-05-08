from __future__ import annotations

import os
import httpx

_HA_API = "http://supervisor/core/api"


def _token() -> str:
    return os.environ.get("SUPERVISOR_TOKEN", "")


async def push_sensor(entity_id: str, state: str | int, attributes: dict) -> None:
    token = _token()
    if not token:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{_HA_API}/states/{entity_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"state": str(state), "attributes": attributes},
            )
    except Exception:
        pass


async def push_fleet_sensors(fleet: dict) -> None:
    staff = fleet.get("staff", [])
    projects = fleet.get("projects", [])
    blocks = fleet.get("blocks", [])
    tasks = fleet.get("tasks", [])

    _inactive = {"done", "halted", "pending", "on vacation", "unavailable", "disabled"}

    def active(items: list) -> int:
        return sum(1 for i in items if i.get("status", "").lower().strip() not in _inactive)

    await push_sensor("sensor.fleet_staff", active(staff), {
        "friendly_name": "Fleet Staff",
        "icon": "mdi:account-group",
        "total": len(staff),
        "hr_count": sum(1 for s in staff if s.get("type") == "HR"),
        "ar_count": sum(1 for s in staff if s.get("type") == "AR"),
        "unit_of_measurement": "members",
        "staff": staff,
    })

    await push_sensor("sensor.fleet_projects", active(projects), {
        "friendly_name": "Fleet Projects",
        "icon": "mdi:folder-multiple-outline",
        "total": len(projects),
        "unit_of_measurement": "projects",
        "projects": projects,
    })

    await push_sensor("sensor.fleet_blocks", active(blocks), {
        "friendly_name": "Fleet Blocks",
        "icon": "mdi:puzzle-outline",
        "total": len(blocks),
        "unit_of_measurement": "blocks",
        "blocks": blocks,
    })

    await push_sensor("sensor.fleet_tasks", active(tasks), {
        "friendly_name": "Fleet Tasks",
        "icon": "mdi:checkbox-multiple-marked-outline",
        "total": len(tasks),
        "unit_of_measurement": "tasks",
        "tasks": tasks,
    })
