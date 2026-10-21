import asyncio
import random

from tests.support import assert_balance_matches_ledger, balance, signed_post, unique


async def test_concurrent_identical_payment_webhooks_credit_once(api, db):
    user_id = random.randint(10**9, 9 * 10**12)
    payment = {"payment_id": unique("pay"), "user_id": user_id, "tokens": 50, "status": "succeeded"}

    responses = await asyncio.gather(*(signed_post(api, "/webhooks/payments", payment) for _ in range(20)))

    assert [r.status_code for r in responses] == [200] * 20
    assert sum(r.json()["credited"] for r in responses) == 1
    assert (await balance(api, user_id))["balance"] == 50
    credits = await db.fetchval("SELECT count(*) FROM ledger WHERE payment_id = $1", payment["payment_id"])
    assert credits == 1
    await assert_balance_matches_ledger(db, user_id)


async def test_payment_with_bad_signature_is_rejected(api):
    response = await api.post(
        "/webhooks/payments",
        json={"payment_id": unique("pay"), "user_id": 1, "tokens": 5, "status": "succeeded"},
        headers={"X-Signature": "0" * 64},
    )
    assert response.status_code == 401


async def test_pending_payment_is_credited_when_it_succeeds(api, db):
    user_id = random.randint(10**9, 9 * 10**12)
    payment = {"payment_id": unique("pay"), "user_id": user_id, "tokens": 7, "status": "pending"}

    pending = await signed_post(api, "/webhooks/payments", payment)
    succeeded = await signed_post(api, "/webhooks/payments", payment | {"status": "succeeded"})
    pending_again = await signed_post(api, "/webhooks/payments", payment)

    assert pending.json()["credited"] is False
    assert succeeded.json()["credited"] is True
    assert pending_again.json()["credited"] is False
    assert (await balance(api, user_id))["balance"] == 7
    await assert_balance_matches_ledger(db, user_id)
