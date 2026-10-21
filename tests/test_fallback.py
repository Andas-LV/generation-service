from tests.support import (
    assert_balance_matches_ledger,
    balance,
    generate,
    ledger_count,
    new_user,
    set_scenario,
    unique,
    wait_for_task,
)


async def test_first_leg_failure_completes_on_second_leg(api, db):
    user_id = await new_user(api, 10)
    prompt = unique("fallback")
    await set_scenario("alpha", prompt, failure_rate=1, failure_type="technical", delay_min=1, delay_max=2)
    await set_scenario("beta", prompt, delay_min=1, delay_max=2)

    task_id = await generate(api, user_id, "image-fast", prompt)
    task = await wait_for_task(api, task_id, lambda t: t["status"] != "processing", timeout=30)

    assert task["status"] == "completed"
    attempts = task["attempts"]
    assert [a["provider"] for a in attempts] == ["alpha", "beta"]
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["error_type"] == "technical"
    assert attempts[1]["status"] == "completed"
    assert task["result_url"] == attempts[1]["result_url"]
    assert task["current_leg"]["provider"] == "beta"
    assert await ledger_count(db, task_id, "refund") == 0
    assert (await balance(api, user_id))["balance"] == 8
    await assert_balance_matches_ledger(db, user_id)


async def test_sync_503_moves_to_next_leg(api, db):
    user_id = await new_user(api, 10)
    prompt = unique("sync503")
    await set_scenario("alpha", prompt, sync_failure_rate=1, sync_failure="503")
    await set_scenario("beta", prompt, delay_min=0.5, delay_max=1)

    task_id = await generate(api, user_id, "image-fast", prompt)
    task = await wait_for_task(api, task_id, lambda t: t["status"] != "processing", timeout=20)

    assert task["status"] == "completed"
    assert [a["provider"] for a in task["attempts"]] == ["alpha", "beta"]
    assert "503" in task["attempts"][0]["error_detail"]


async def test_moderation_skips_legs_marked_skip_on_moderation(api, db):
    user_id = await new_user(api, 30)
    prompt = unique("moderation")
    await set_scenario("beta", prompt, failure_rate=1, failure_type="moderation", delay_min=0.5, delay_max=1)

    task_id = await generate(api, user_id, "video-long", prompt)
    task = await wait_for_task(api, task_id, lambda t: t["status"] != "processing", timeout=20)

    assert task["status"] == "failed"
    assert [a["provider"] for a in task["attempts"]] == ["beta"]
    assert await ledger_count(db, task_id, "refund") == 1
    assert (await balance(api, user_id))["balance"] == 30


async def test_duplicate_success_webhooks_are_harmless(api, db):
    user_id = await new_user(api, 10)
    prompt = unique("duplicate")
    await set_scenario("alpha", prompt, duplicate_rate=1, delay_min=0.5, delay_max=1)

    task_id = await generate(api, user_id, "image-fast", prompt)
    task = await wait_for_task(api, task_id, lambda t: t["status"] != "processing", timeout=20)

    assert task["status"] == "completed"
    assert len(task["attempts"]) == 1
    assert (await balance(api, user_id))["balance"] == 8
    await assert_balance_matches_ledger(db, user_id)
