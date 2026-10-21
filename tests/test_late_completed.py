import asyncio

from tests.support import (
    PROVIDER_TIMEOUT,
    assert_balance_matches_ledger,
    balance,
    generate,
    ledger_count,
    new_user,
    set_scenario,
    unique,
    wait_for_task,
)

LATE_DELAY = PROVIDER_TIMEOUT + 10
WAIT = PROVIDER_TIMEOUT + 40


async def late_result_while_second_leg_runs(api, db):
    user_id = await new_user(api, 10)
    prompt = unique("late-accepted")
    await set_scenario("alpha", prompt, delay_min=LATE_DELAY, delay_max=LATE_DELAY)
    await set_scenario("beta", prompt, drop_rate=1, delay_min=0, delay_max=0)

    task_id = await generate(api, user_id, "image-fast", prompt)
    task = await wait_for_task(api, task_id, lambda t: t["status"] != "processing", timeout=WAIT)

    assert task["status"] == "completed"
    alpha, beta = task["attempts"]
    assert alpha["provider"] == "alpha"
    assert alpha["status"] == "completed"
    assert alpha["late"] is True
    assert alpha["error_detail"] == "provider did not respond in time"
    assert beta["provider"] == "beta"
    assert beta["status"] == "cancelled"
    assert task["result_url"] == alpha["result_url"]
    assert await ledger_count(db, task_id, "refund") == 0
    assert (await balance(api, user_id))["balance"] == 8
    await assert_balance_matches_ledger(db, user_id)


async def late_result_after_refund(api, db):
    user_id = await new_user(api, 10)
    prompt = unique("late-discarded")
    await set_scenario("alpha", prompt, delay_min=LATE_DELAY, delay_max=LATE_DELAY)
    await set_scenario("beta", prompt, failure_rate=1, failure_type="technical", delay_min=0.5, delay_max=1)

    task_id = await generate(api, user_id, "image-fast", prompt)
    await wait_for_task(api, task_id, lambda t: t["status"] == "failed", timeout=WAIT)
    task = await wait_for_task(api, task_id, lambda t: t["attempts"][0]["late"], timeout=WAIT)

    assert task["status"] == "failed"
    assert task["result_url"] is None
    alpha, beta = task["attempts"]
    assert alpha["status"] == "timed_out"
    assert alpha["result_url"] is not None
    assert beta["status"] == "failed"
    assert await ledger_count(db, task_id, "refund") == 1
    assert (await balance(api, user_id))["balance"] == 10
    await assert_balance_matches_ledger(db, user_id)


async def test_late_completed_after_watchdog(api, db):
    await asyncio.gather(
        late_result_while_second_leg_runs(api, db),
        late_result_after_refund(api, db),
    )
