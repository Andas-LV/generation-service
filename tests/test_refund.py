import asyncio

from tests.support import (
    assert_balance_matches_ledger,
    balance,
    generate,
    ledger_count,
    new_user,
    set_scenario,
    signed_post,
    unique,
    wait_for_task,
)


async def test_concurrent_failures_on_last_leg_refund_once(api, db):
    user_id = await new_user(api, 10)
    prompt = unique("refund")
    await set_scenario("beta", prompt, drop_rate=1, delay_min=0, delay_max=0)

    task_id = await generate(api, user_id, "image-fast", prompt, refs=5)
    task = await wait_for_task(api, task_id, lambda t: t["attempts"][-1]["status"] == "waiting", timeout=10)

    assert len(task["attempts"]) == 1
    attempt = task["attempts"][0]
    assert attempt["provider"] == "beta"
    assert (await balance(api, user_id))["balance"] == 8

    payload = {
        "external_id": attempt["external_id"],
        "reference": attempt["id"],
        "status": "failed",
        "error_type": "technical",
    }
    responses = await asyncio.gather(
        *(signed_post(api, "/webhooks/providers/beta", payload) for _ in range(10))
    )

    assert [r.status_code for r in responses] == [200] * 10
    outcomes = sorted(r.json()["outcome"] for r in responses)
    assert outcomes == ["failed"] + ["ignored"] * 9

    task = (await api.get(f"/tasks/{task_id}")).json()
    assert task["status"] == "failed"
    assert task["result_url"] is None
    assert await ledger_count(db, task_id, "refund") == 1
    assert (await balance(api, user_id))["balance"] == 10
    await assert_balance_matches_ledger(db, user_id)


async def test_payload_error_fails_task_without_trying_next_leg(api, db):
    user_id = await new_user(api, 10)
    prompt = unique("payload")
    await set_scenario("alpha", prompt, failure_rate=1, failure_type="payload", delay_min=0.5, delay_max=1)

    task_id = await generate(api, user_id, "image-fast", prompt)
    task = await wait_for_task(api, task_id, lambda t: t["status"] != "processing", timeout=15)

    assert task["status"] == "failed"
    assert [a["provider"] for a in task["attempts"]] == ["alpha"]
    assert task["attempts"][0]["error_type"] == "payload"
    assert await ledger_count(db, task_id, "refund") == 1
    assert (await balance(api, user_id))["balance"] == 10
